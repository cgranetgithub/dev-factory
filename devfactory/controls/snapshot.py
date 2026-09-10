"""Read the enforced control configuration of a repository and normalise it.

Everything here goes through the ``gh`` singleton, as the project requires. Two
of the five calls have no PyGithub binding (PyGithub 2.9 exposes no repository
*rulesets* API, only the legacy branch-protection one), so they reuse the
``Requester`` that the singleton's ``Repository`` object already holds. That is
still one authenticated client with one token and one set of retry/rate-limit
settings — the rule this project cares about — rather than a second HTTP client
with its own credentials.

The result is a plain, JSON-serialisable dict, canonicalised so that two readings
of an unchanged configuration hash identically whatever order GitHub returned
its keys and lists in.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, cast

from github.GithubException import GithubException

from devfactory.github.client import gh

logger = logging.getLogger(__name__)

# Where a CODEOWNERS file is allowed to live, in GitHub's own precedence order.
# The first one that exists is the one GitHub enforces, so we stop there.
CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")


class SnapshotError(RuntimeError):
    """A GitHub call needed for the snapshot could not be read.

    Attributes:
        call: Human-readable name of the call that failed, so the CLI can say
            which one rather than only that "the API failed".
    """

    def __init__(self, call: str, cause: Exception):
        super().__init__(f"GitHub call {call!r} failed: {cause}")
        self.call = call
        self.cause = cause


def take_snapshot(repo_full_name: str) -> dict[str, Any]:
    """Read the control configuration of ``repo_full_name`` and canonicalise it.

    Args:
        repo_full_name: Repository in ``owner/repo`` form.

    Returns:
        A canonical, JSON-serialisable dict describing the enforced configuration
        of the default branch.

    Raises:
        SnapshotError: If any of the underlying GitHub calls fails.
    """
    repo = _call("GET /repos/{repo}", lambda: gh.get_repo(repo_full_name))
    default_branch = repo.default_branch

    snapshot: dict[str, Any] = {
        "repo": repo_full_name,
        "owner": repo.owner.login,
        "default_branch": default_branch,
        # Auto-merge is part of the control surface: it is what lets the factory
        # arm a merge that fires the moment the human approval lands.
        "allow_auto_merge": bool(repo.allow_auto_merge),
        "rulesets": _rulesets(repo, repo_full_name, default_branch),
        "effective_rules": _effective_rules(repo, repo_full_name, default_branch),
        "collaborators": _collaborators(repo),
        "codeowners": _codeowners(repo, default_branch),
    }
    # canonicalise() walks arbitrary JSON, so it is typed Any; the top level of a
    # snapshot is always the dict built above.
    return cast(dict[str, Any], canonicalise(snapshot))


# ── The individual readings ───────────────────────────────────────────────────


def _rulesets(repo: Any, repo_full_name: str, default_branch: str) -> dict[str, Any]:
    """Return the rulesets targeting the default branch, keyed by ruleset name.

    Keyed by name rather than by id because the name is what a human reading the
    evidence recognises: ``rulesets.PR.rules.pull_request.required_approving_review_count``
    is a sentence, ``rulesets.18296256...`` is not. The id travels inside the
    entry, so a rename is still visible in the drift (as one ruleset gone and
    another arrived, with the same id).
    """
    listing = _call(
        "GET /repos/{repo}/rulesets",
        lambda: repo._requester.requestJsonAndCheck("GET", f"/repos/{repo_full_name}/rulesets"),
    )[1]

    out: dict[str, Any] = {}
    for entry in listing or []:
        ruleset_id = entry.get("id")
        # The listing carries no rules or bypass actors — only the detail endpoint does.
        detail = _call(
            f"GET /repos/{{repo}}/rulesets/{ruleset_id}",
            lambda rid=ruleset_id: repo._requester.requestJsonAndCheck(
                "GET", f"/repos/{repo_full_name}/rulesets/{rid}"
            ),
        )[1]

        if not _targets_default_branch(detail, default_branch):
            continue

        out[str(detail.get("name", ruleset_id))] = {
            "id": ruleset_id,
            "target": detail.get("target"),
            "enforcement": detail.get("enforcement"),
            "conditions": detail.get("conditions") or {},
            # Absent from the payload when nobody may bypass; an empty list and a
            # missing key mean the same thing, so record the same thing.
            "bypass_actors": detail.get("bypass_actors") or [],
            "rules": _rules_by_type(detail.get("rules") or []),
        }
    return out


def _targets_default_branch(detail: dict[str, Any], default_branch: str) -> bool:
    """Whether a ruleset applies to the default branch.

    GitHub expresses this either through the ``~DEFAULT_BRANCH`` alias or through
    an explicit ``refs/heads/<branch>`` pattern; ``~ALL`` covers everything.
    """
    if detail.get("target") not in (None, "branch"):
        return False
    ref_name = (detail.get("conditions") or {}).get("ref_name") or {}
    include = ref_name.get("include") or []
    return any(
        pattern in ("~DEFAULT_BRANCH", "~ALL", f"refs/heads/{default_branch}")
        for pattern in include
    )


def _rules_by_type(rules: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn GitHub's list of rules into a dict keyed by rule type.

    A ruleset holds at most one rule of each type, so the type is a stable key —
    and it makes the drift path name the control (``rules.pull_request.…``)
    instead of an array index that shifts when an unrelated rule is added.
    """
    out: dict[str, Any] = {}
    for rule in rules:
        rule_type = str(rule.get("type"))
        # Rules such as `deletion` carry no parameters; keep the empty dict so a
        # removed rule shows up as a removal rather than as nothing at all.
        out[rule_type] = rule.get("parameters") or {}
    return out


def _effective_rules(repo: Any, repo_full_name: str, branch: str) -> dict[str, Any]:
    """Return what GitHub actually enforces on ``branch``, after merging rulesets.

    This is the reading that matters most: the per-ruleset view above can look
    correct while an overlapping ruleset, an organisation-level one or a bypass
    changes the effective outcome.
    """
    entries = _call(
        f"GET /repos/{{repo}}/rules/branches/{branch}",
        lambda: repo._requester.requestJsonAndCheck(
            "GET", f"/repos/{repo_full_name}/rules/branches/{branch}"
        ),
    )[1]

    # Several rulesets can contribute the same rule type. Key by type while it is
    # unique — the readable case — and disambiguate with the source ruleset id
    # only when it is not.
    by_type: dict[str, list[dict[str, Any]]] = {}
    for entry in entries or []:
        by_type.setdefault(str(entry.get("type")), []).append(entry)

    out: dict[str, Any] = {}
    for rule_type, group in by_type.items():
        for entry in group:
            key = rule_type if len(group) == 1 else f"{rule_type}#{entry.get('ruleset_id')}"
            out[key] = {
                "ruleset_id": entry.get("ruleset_id"),
                "ruleset_source": entry.get("ruleset_source"),
                "parameters": entry.get("parameters") or {},
            }
    return out


def _collaborators(repo: Any) -> dict[str, str]:
    """Return ``{login: role_name}`` for every collaborator.

    Who holds ``admin`` is a control in itself: an admin can rewrite the ruleset,
    so the factory's own account gaining admin is exactly the drift we watch for.
    """
    collaborators = _call("GET /repos/{repo}/collaborators", lambda: list(repo.get_collaborators()))
    return {user.login: str(user.role_name) for user in collaborators}


def _codeowners(repo: Any, ref: str) -> dict[str, Any]:
    """Return the CODEOWNERS location and a hash of its content.

    The content is hashed rather than stored: the file names the humans who must
    approve, and the evidence only needs to show whether that set changed —
    copying reviewer identities into the KB adds nothing an auditor asked for.
    """
    for path in CODEOWNERS_PATHS:
        try:
            contents = repo.get_contents(path, ref=ref)
        except GithubException as exc:
            if exc.status == 404:
                continue
            raise SnapshotError(f"GET /repos/{{repo}}/contents/{path}", exc) from exc
        raw = contents.decoded_content
        return {
            "present": True,
            "path": path,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    return {"present": False, "path": None, "sha256": None}


def _call(name: str, fn: Any) -> Any:
    """Run one GitHub read, turning any API failure into a named SnapshotError."""
    try:
        return fn()
    except GithubException as exc:
        logger.error(f"[controls] {name} failed: {exc}")
        raise SnapshotError(name, exc) from exc


# ── Canonicalisation ──────────────────────────────────────────────────────────


def canonicalise(value: Any) -> Any:
    """Return ``value`` with every dict key and every list sorted, recursively.

    Two readings of an unchanged configuration must produce the same bytes, and
    GitHub guarantees the order of neither its JSON keys nor its arrays. Lists
    are sorted by the canonical JSON of their items, which orders heterogeneous
    items deterministically without needing a per-field sort key.
    """
    if isinstance(value, dict):
        return {k: canonicalise(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, list):
        items = [canonicalise(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    return value


def canonical_json(snapshot: dict[str, Any]) -> str:
    """Serialise a snapshot to its canonical JSON form (the hashed representation)."""
    return json.dumps(canonicalise(snapshot), sort_keys=True, separators=(",", ":"), default=str)


def snapshot_sha256(snapshot: dict[str, Any]) -> str:
    """Return the SHA-256 of a snapshot's canonical JSON."""
    return hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()
