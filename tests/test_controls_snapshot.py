"""Tests for the control snapshot, the drift comparison, its storage and the CLI.

GitHub is mocked end to end: the suite must never touch the network, and the
fixtures below are trimmed copies of real payloads read from
``cgranetgithub/dev-factory``.
"""

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, cast

import pytest
from github.GithubException import GithubException
from typer.testing import CliRunner

from devfactory.cli import app
from devfactory.controls.check import check_repository
from devfactory.controls.drift import compute_drift, format_change
from devfactory.controls.snapshot import canonicalise, snapshot_sha256
from devfactory.kb.database import Database

# ── Fixtures: trimmed real payloads ───────────────────────────────────────────

RULESET_LIST = [{"id": 18296256, "name": "PR", "target": "branch", "enforcement": "active"}]

RULESET_DETAIL: dict[str, Any] = {
    "id": 18296256,
    "name": "PR",
    "target": "branch",
    "enforcement": "active",
    "conditions": {"ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH"]}},
    "rules": [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {
            "type": "pull_request",
            "parameters": {
                "required_approving_review_count": 1,
                "dismiss_stale_reviews_on_push": True,
                "required_reviewers": [],
                "require_code_owner_review": True,
                "require_last_push_approval": True,
            },
        },
    ],
}

BRANCH_RULES = [
    {"type": "deletion", "ruleset_source": "o/r", "ruleset_id": 18296256},
    {"type": "non_fast_forward", "ruleset_source": "o/r", "ruleset_id": 18296256},
    {
        "type": "pull_request",
        "ruleset_source": "o/r",
        "ruleset_id": 18296256,
        "parameters": {"required_approving_review_count": 1},
    },
]

CODEOWNERS_BODY = b"* @cgranetgithub\n"


class FakeRequester:
    """Stands in for the ``Requester`` the PyGithub Repository holds."""

    def __init__(self, ruleset_detail, branch_rules, fail_on: str | None = None):
        self.ruleset_detail = ruleset_detail
        self.branch_rules = branch_rules
        self.fail_on = fail_on

    def requestJsonAndCheck(self, method, url):  # noqa: N802 — PyGithub's own name
        if self.fail_on and self.fail_on in url:
            raise GithubException(500, {"message": "boom"}, None)
        if url.endswith("/rulesets"):
            return {}, RULESET_LIST
        if "/rulesets/" in url:
            return {}, self.ruleset_detail
        if "/rules/branches/" in url:
            return {}, self.branch_rules
        raise AssertionError(f"unexpected url {url}")


class FakeUser:
    def __init__(self, login, role_name="write"):
        self.login = login
        self.role_name = role_name


class FakeContents:
    def __init__(self, body: bytes):
        self.decoded_content = body


class FakeRepo:
    """A PyGithub Repository, reduced to what the snapshot reads."""

    def __init__(
        self,
        ruleset_detail=None,
        branch_rules=None,
        collaborators=None,
        codeowners=CODEOWNERS_BODY,
        fail_on=None,
    ):
        self.default_branch = "main"
        self.allow_auto_merge = True
        self.owner = FakeUser("cgranetgithub", "admin")
        self._collaborators = collaborators or [
            FakeUser("cgranetgithub", "admin"),
            FakeUser("bot-bobby", "write"),
        ]
        self._codeowners = codeowners
        self._requester = FakeRequester(
            ruleset_detail if ruleset_detail is not None else RULESET_DETAIL,
            branch_rules if branch_rules is not None else BRANCH_RULES,
            fail_on=fail_on,
        )

    def get_collaborators(self):
        return list(self._collaborators)

    def get_contents(self, path, ref=None):
        if path == ".github/CODEOWNERS" and self._codeowners is not None:
            return FakeContents(self._codeowners)
        raise GithubException(404, {"message": "Not Found"}, None)


def patch_gh(monkeypatch, repo: FakeRepo):
    """Point the ``gh`` singleton, as the snapshot module imported it, at a fake repo."""
    monkeypatch.setattr(
        "devfactory.controls.snapshot.gh.get_repo", lambda full_name: repo, raising=False
    )


def make_db() -> Database:
    """A throwaway file-backed database, injected the way the other tests do."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return Database(path=Path(tmp.name))


# ── Canonicalisation ──────────────────────────────────────────────────────────


def test_same_state_in_any_order_hashes_identically():
    """Key order and list order are GitHub's business, not a control change."""
    one = {
        "rulesets": {"PR": {"enforcement": "active", "bypass_actors": [{"id": 2}, {"id": 1}]}},
        "collaborators": {"bot-bobby": "write", "cgranetgithub": "admin"},
    }
    other = {
        "collaborators": {"cgranetgithub": "admin", "bot-bobby": "write"},
        "rulesets": {"PR": {"bypass_actors": [{"id": 1}, {"id": 2}], "enforcement": "active"}},
    }
    assert snapshot_sha256(one) == snapshot_sha256(other)


def test_a_real_change_changes_the_hash():
    base = {"rulesets": {"PR": {"rules": {"pull_request": {"count": 1}}}}}
    weakened = {"rulesets": {"PR": {"rules": {"pull_request": {"count": 0}}}}}
    assert snapshot_sha256(base) != snapshot_sha256(weakened)


def test_snapshot_reads_every_control_surface(monkeypatch):
    from devfactory.controls.snapshot import take_snapshot

    patch_gh(monkeypatch, FakeRepo())
    snap = take_snapshot("o/r")

    assert snap["default_branch"] == "main"
    assert snap["allow_auto_merge"] is True
    assert snap["collaborators"] == {"bot-bobby": "write", "cgranetgithub": "admin"}
    assert snap["rulesets"]["PR"]["enforcement"] == "active"
    assert snap["rulesets"]["PR"]["rules"]["pull_request"]["required_approving_review_count"] == 1
    # Parameterless rules are kept as empty dicts so their removal is visible.
    assert snap["rulesets"]["PR"]["rules"]["non_fast_forward"] == {}
    assert snap["effective_rules"]["pull_request"]["ruleset_id"] == 18296256
    assert snap["codeowners"]["present"] is True
    assert snap["codeowners"]["path"] == ".github/CODEOWNERS"
    assert len(snap["codeowners"]["sha256"]) == 64


def test_missing_codeowners_is_recorded_as_absent(monkeypatch):
    from devfactory.controls.snapshot import take_snapshot

    patch_gh(monkeypatch, FakeRepo(codeowners=None))
    snap = take_snapshot("o/r")
    assert snap["codeowners"] == {"present": False, "path": None, "sha256": None}


def test_a_ruleset_that_ignores_the_default_branch_is_skipped(monkeypatch):
    from devfactory.controls.snapshot import take_snapshot

    elsewhere = dict(RULESET_DETAIL)
    elsewhere["conditions"] = {"ref_name": {"exclude": [], "include": ["refs/heads/release/*"]}}
    patch_gh(monkeypatch, FakeRepo(ruleset_detail=elsewhere))
    assert take_snapshot("o/r")["rulesets"] == {}


def test_a_failed_call_names_itself(monkeypatch):
    from devfactory.controls.snapshot import SnapshotError, take_snapshot

    patch_gh(monkeypatch, FakeRepo(fail_on="/rules/branches/"))
    with pytest.raises(SnapshotError) as excinfo:
        take_snapshot("o/r")
    assert "rules/branches" in excinfo.value.call


# ── Drift ─────────────────────────────────────────────────────────────────────


def _snapshot(**overrides) -> dict[str, Any]:
    """A minimal but realistic snapshot, canonicalised like the real one."""
    base: dict[str, Any] = {
        "repo": "o/r",
        "default_branch": "main",
        "rulesets": {
            "PR": {
                "enforcement": "active",
                "bypass_actors": [],
                "rules": {
                    "non_fast_forward": {},
                    "pull_request": {"required_approving_review_count": 1},
                },
            }
        },
        "collaborators": {"cgranetgithub": "admin", "bot-bobby": "write"},
        "codeowners": {"present": True, "path": ".github/CODEOWNERS", "sha256": "aaa"},
    }
    base.update(overrides)
    return cast(dict[str, Any], canonicalise(base))


def test_no_change_produces_no_drift():
    assert compute_drift(_snapshot(), _snapshot()) == []


def test_a_baseline_has_nothing_to_drift_from():
    assert compute_drift(None, _snapshot()) == []


def test_lowered_approval_count_is_one_dotted_path():
    before = _snapshot()
    after = _snapshot()
    after["rulesets"]["PR"]["rules"]["pull_request"]["required_approving_review_count"] = 0

    changes = compute_drift(before, after)

    assert len(changes) == 1
    assert changes[0]["path"] == ("rulesets.PR.rules.pull_request.required_approving_review_count")
    assert (changes[0]["before"], changes[0]["after"]) == (1, 0)
    assert format_change(changes[0]).endswith("1 -> 0")


def test_an_added_bypass_actor_is_reported_as_an_addition():
    before = _snapshot()
    after = _snapshot()
    after["rulesets"]["PR"]["bypass_actors"] = [{"actor_id": 5, "bypass_mode": "always"}]

    changes = compute_drift(before, after)

    assert [c["kind"] for c in changes] == ["added"]
    assert changes[0]["path"] == "rulesets.PR.bypass_actors"
    assert changes[0]["after"] == {"actor_id": 5, "bypass_mode": "always"}


def test_a_removed_collaborator_is_reported_as_a_removal():
    before = _snapshot()
    after = _snapshot()
    del after["collaborators"]["bot-bobby"]

    changes = compute_drift(before, after)

    assert len(changes) == 1
    assert changes[0]["path"] == "collaborators.bot-bobby"
    assert changes[0]["kind"] == "removed"
    assert changes[0]["before"] == "write"


def test_a_codeowners_edit_shows_up_as_a_hash_change():
    before = _snapshot()
    after = _snapshot()
    after["codeowners"]["sha256"] = "bbb"

    changes = compute_drift(before, after)

    assert [(c["path"], c["kind"]) for c in changes] == [("codeowners.sha256", "changed")]


def test_a_dropped_parameterless_rule_is_not_lost():
    before = _snapshot()
    after = _snapshot()
    del after["rulesets"]["PR"]["rules"]["non_fast_forward"]

    changes = compute_drift(before, after)

    assert [(c["path"], c["kind"]) for c in changes] == [
        ("rulesets.PR.rules.non_fast_forward", "removed")
    ]


# ── Storage ───────────────────────────────────────────────────────────────────


def test_two_checks_are_two_linked_rows(monkeypatch):
    db = make_db()
    patch_gh(monkeypatch, FakeRepo())

    first = check_repository("o/r", database=db)
    second = check_repository("o/r", database=db)

    rows = db.control_snapshots("o/r")
    assert len(rows) == 2
    assert first.baseline is True and second.baseline is False
    # The baseline records NULL drift — "never compared" is not "compared, no change".
    assert rows[1]["drift_json"] is None
    assert rows[0]["drift_json"] == "[]"
    assert rows[0]["previous_id"] == first.snapshot_id
    assert first.sha256 == second.sha256


def test_a_weakened_ruleset_is_stored_as_drift(monkeypatch):
    db = make_db()
    patch_gh(monkeypatch, FakeRepo())
    check_repository("o/r", database=db)

    weakened = json.loads(json.dumps(RULESET_DETAIL))
    weakened["rules"][2]["parameters"]["required_approving_review_count"] = 0
    patch_gh(monkeypatch, FakeRepo(ruleset_detail=weakened))
    second = check_repository("o/r", database=db)

    assert second.drifted
    paths = [c["path"] for c in second.changes]
    assert "rulesets.PR.rules.pull_request.required_approving_review_count" in paths
    stored = json.loads(db.control_snapshots("o/r")[0]["drift_json"])
    assert stored == second.changes


def test_the_database_offers_no_way_to_rewrite_a_record():
    """Immutability is the point: an editable audit trail is not an audit trail."""
    db = make_db()
    exposed = [name for name in dir(db) if "control" in name]
    assert sorted(exposed) == [
        "control_snapshots",
        "latest_control_snapshot",
        "record_control_snapshot",
    ]


def test_the_table_survives_reopening_an_existing_database(monkeypatch):
    """A devfactory.db written before this feature must gain the table on open."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = Path(tmp.name)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS models (id INTEGER PRIMARY KEY, name TEXT UNIQUE)")
    conn.commit()
    conn.close()

    db = Database(path=path)
    patch_gh(monkeypatch, FakeRepo())
    assert check_repository("o/r", database=db).snapshot_id > 0


# ── CLI ───────────────────────────────────────────────────────────────────────


def _cli(monkeypatch, db: Database, repo: FakeRepo, *args: str):
    """Run `devfactory controls check` against a fake GitHub and an injected DB."""
    patch_gh(monkeypatch, repo)
    # The command imports check_repository from the package at call time, so
    # patching it there is enough to inject the test database — otherwise the run
    # would write into the developer's real devfactory.db.
    monkeypatch.setattr(
        "devfactory.controls.check_repository",
        lambda repo_name, database=None: check_repository(repo_name, database=db),
    )
    return CliRunner().invoke(app, ["controls", "check", "--repo", "o/r", *args])


def test_cli_exits_zero_on_a_baseline(monkeypatch):
    result = _cli(monkeypatch, make_db(), FakeRepo())
    assert result.exit_code == 0, result.output
    assert "Baseline recorded" in result.output


def test_cli_exits_one_on_drift(monkeypatch):
    db = make_db()
    assert _cli(monkeypatch, db, FakeRepo()).exit_code == 0

    weakened = json.loads(json.dumps(RULESET_DETAIL))
    weakened["rules"][2]["parameters"]["required_approving_review_count"] = 0
    result = _cli(monkeypatch, db, FakeRepo(ruleset_detail=weakened))

    assert result.exit_code == 1, result.output
    assert "required_approving_review_count" in result.output


def test_cli_exits_two_when_github_cannot_be_read(monkeypatch):
    result = _cli(monkeypatch, make_db(), FakeRepo(fail_on="/rulesets"))
    assert result.exit_code == 2, result.output
    assert "rulesets" in result.output


def test_cli_json_mode_prints_the_canonical_snapshot(monkeypatch):
    result = _cli(monkeypatch, make_db(), FakeRepo(), "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["snapshot"]["rulesets"]["PR"]["enforcement"] == "active"
    assert payload["drift"] == []
