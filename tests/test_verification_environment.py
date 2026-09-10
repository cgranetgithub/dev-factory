"""
Tests for how the gate reads a target repository (issue #92).

The verification image ships uv and no fixed Python, so the interpreter and the
install command are decided here, on the host, from what the repository declares.
That decision ends up in a run's evidence, which is why it is resolved in Python
and asserted here rather than improvised by a shell script in a container.

The profile file is the other half: a versioned, per-repository override. A typo
in it must be loud, so several of these tests are about refusing bad input.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devfactory.verification.environment import DEFAULT_PYTHON_VERSION, resolve_environment
from devfactory.verification.profiles import (
    PROFILES_PATH,
    ProfileError,
    VerificationProfile,
    load_profile,
)

# The declarations a target can carry, as content for the files that hold them.
_PY312 = '[project]\nrequires-python = ">=3.12"\n'
_PY311 = '[project]\nrequires-python = ">=3.11"\n'
_EXTRAS = '[project.optional-dependencies]\ntest = ["pytest"]\naudit = ["anthropic"]\n'


def _repo(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    """A checkout containing exactly the declaration files a test needs."""
    for name, content in (files or {}).items():
        (tmp_path / name).write_text(content)
    return tmp_path


# ── The interpreter ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("requires", "expected"),
    [
        (">=3.12,<4", "3.12"),
        (">=3.11", "3.11"),
        (">=3.12", "3.12"),
        ("~=3.13.0", "3.13"),
        (">=3.9", "3.11"),  # the oldest the image caches still satisfies it
        ("==3.13.*", "3.13"),
    ],
)
def test_the_interpreter_comes_from_requires_python(tmp_path, requires, expected):
    """The lowest version satisfying the specifier: the floor the repository
    claims, which is also what its own resolver picks by default."""
    repo = _repo(tmp_path, {"pyproject.toml": f'[project]\nrequires-python = "{requires}"\n'})

    env = resolve_environment(repo, VerificationProfile())

    assert env.python_version == expected
    assert env.python_source == f"requires-python {requires}"


def test_a_requires_python_ahead_of_the_image_is_honoured_anyway(tmp_path):
    """A target on a version we do not cache is not a target we refuse: uv fetches
    the interpreter, and the log says it will cost a download per run."""
    repo = _repo(tmp_path, {"pyproject.toml": '[project]\nrequires-python = ">=3.19"\n'})

    env = resolve_environment(repo, VerificationProfile())

    assert env.python_version == "3.19"


def test_a_meaningless_requires_python_falls_back_to_the_default(tmp_path):
    repo = _repo(tmp_path, {"pyproject.toml": '[project]\nrequires-python = "banana"\n'})

    env = resolve_environment(repo, VerificationProfile())

    assert env.python_version == DEFAULT_PYTHON_VERSION
    assert env.python_source == "the documented default"


def test_a_python_version_file_is_honoured(tmp_path):
    """uv's own convention: a repository that pins its interpreter there has
    already answered the question."""
    repo = _repo(tmp_path, {".python-version": "3.13\n", "pyproject.toml": _PY311})

    env = resolve_environment(repo, VerificationProfile())

    assert env.python_version == "3.13"
    assert env.python_source == ".python-version"


def test_the_profile_wins_over_everything_the_repository_says(tmp_path):
    repo = _repo(tmp_path, {"pyproject.toml": _PY311})

    env = resolve_environment(repo, VerificationProfile(python_version="3.14"))

    assert env.python_version == "3.14"
    assert env.python_source == "the profile"


def test_a_repository_declaring_no_interpreter_gets_the_documented_default(tmp_path):
    env = resolve_environment(_repo(tmp_path), VerificationProfile())

    assert env.python_version == DEFAULT_PYTHON_VERSION
    assert env.python_source == "the documented default"


# ── The dependency shape ─────────────────────────────────────────────────────


def test_a_lockfile_wins_over_the_pyproject_that_produced_it(tmp_path):
    repo = _repo(tmp_path, {"pyproject.toml": _PY312, "uv.lock": "version = 1\n"})

    env = resolve_environment(repo, VerificationProfile())

    assert env.install_source == "uv.lock"
    assert env.install == ("uv sync --frozen --python 3.12",)


def test_the_test_extras_a_repository_declares_are_installed(tmp_path):
    """A suite cannot run without its test dependencies, and `test` means that by
    convention. `audit` does not, so it is left to the profile."""
    repo = _repo(tmp_path, {"pyproject.toml": _PY312 + _EXTRAS, "uv.lock": "version = 1\n"})

    env = resolve_environment(repo, VerificationProfile())

    assert env.extras == ("test",)
    assert env.install == ("uv sync --frozen --python 3.12 --extra test",)


def test_the_profile_replaces_the_derived_extras(tmp_path):
    """news-watch's case: its own suite imports a module from an extra no
    convention would have found."""
    repo = _repo(tmp_path, {"pyproject.toml": _PY312 + _EXTRAS, "uv.lock": "version = 1\n"})

    env = resolve_environment(repo, VerificationProfile(extras=["test", "audit"]))

    assert env.install == ("uv sync --frozen --python 3.12 --extra test --extra audit",)


def test_a_bare_pyproject_is_installed_editable(tmp_path):
    repo = _repo(
        tmp_path,
        {"pyproject.toml": _PY311 + '[project.optional-dependencies]\ndev = ["pytest"]\n'},
    )

    env = resolve_environment(repo, VerificationProfile())

    assert env.install_source == "pyproject.toml"
    assert env.install == ("uv pip install -e '.[dev]'",)


def test_a_pyproject_that_cannot_be_parsed_is_still_the_declared_shape(tmp_path):
    """Falling back to "nothing declared" would hide the real problem behind a
    test collection error. The install fails instead, and the gate says so."""
    repo = _repo(tmp_path, {"pyproject.toml": "[project\nbroken"})

    env = resolve_environment(repo, VerificationProfile())

    assert env.install_source == "pyproject.toml"
    assert env.install == ("uv pip install -e .",)


def test_requirements_and_their_dev_companions_are_installed(tmp_path):
    repo = _repo(
        tmp_path, {"requirements.txt": "numpy>=2\n", "requirements-dev.txt": "pytest>=8\n"}
    )

    env = resolve_environment(repo, VerificationProfile())

    assert env.install_source == "requirements.txt"
    assert env.install == ("uv pip install -r requirements.txt -r requirements-dev.txt",)


def test_requirements_alone_are_enough(tmp_path):
    repo = _repo(tmp_path, {"requirements.txt": "numpy>=2\n"})

    env = resolve_environment(repo, VerificationProfile())

    assert env.install == ("uv pip install -r requirements.txt",)


def test_a_repository_declaring_no_dependencies_installs_nothing(tmp_path):
    env = resolve_environment(_repo(tmp_path), VerificationProfile())

    assert env.install == ()
    assert env.install_source == "nothing declared"
    # The virtualenv is still created: pytest has to live somewhere.
    assert env.commands == (
        f"uv python install {DEFAULT_PYTHON_VERSION}",
        f"uv venv --python {DEFAULT_PYTHON_VERSION} /build/.venv",
    )


def test_the_profile_can_replace_the_install_entirely(tmp_path):
    repo = _repo(tmp_path, {"requirements.txt": "numpy>=2\n"})

    env = resolve_environment(repo, VerificationProfile(install=["uv sync --all-extras"]))

    assert env.install_source == "the profile"
    assert env.commands[-1] == "uv sync --all-extras"
    # Provisioning the interpreter is not something an override has to repeat.
    assert env.commands[0] == f"uv python install {DEFAULT_PYTHON_VERSION}"


# ── The profile file ─────────────────────────────────────────────────────────


def test_a_repository_with_no_entry_gets_pure_defaults(tmp_path):
    path = tmp_path / "verification.toml"
    path.write_text('["someone/else"]\npython_version = "3.14"\n')

    profile = load_profile("owner/unknown", path)

    assert profile == VerificationProfile()
    assert profile.tools == ["ruff", "mypy", "bandit", "pytest"]
    assert profile.skipped_tools == []


def test_a_missing_profile_file_is_not_an_error(tmp_path):
    assert load_profile("owner/repo", tmp_path / "absent.toml") == VerificationProfile()


def test_the_slug_is_matched_case_insensitively(tmp_path):
    path = tmp_path / "verification.toml"
    path.write_text('["Owner/Repo"]\npython_version = "3.13"\n')

    assert load_profile("owner/repo", path).python_version == "3.13"


def test_an_unknown_key_is_refused(tmp_path):
    """A silently ignored line would mean the gate was configured by nobody."""
    path = tmp_path / "verification.toml"
    path.write_text('["owner/repo"]\npythn_version = "3.13"\n')

    with pytest.raises(ProfileError, match="pythn_version"):
        load_profile("owner/repo", path)


def test_an_unknown_tool_is_refused(tmp_path):
    path = tmp_path / "verification.toml"
    path.write_text('["owner/repo"]\ntools = ["ruff", "pylint"]\n')

    with pytest.raises(ProfileError, match="pylint"):
        load_profile("owner/repo", path)


def test_a_version_that_is_not_one_is_refused(tmp_path):
    path = tmp_path / "verification.toml"
    path.write_text('["owner/repo"]\npython_version = "latest"\n')

    with pytest.raises(ProfileError, match="python_version"):
        load_profile("owner/repo", path)


def test_a_broken_entry_for_another_repository_is_still_refused(tmp_path):
    """The defect is in this file, and the run that notices it should say so
    rather than leave it for the day that repository is next verified."""
    path = tmp_path / "verification.toml"
    path.write_text('["someone/else"]\ntools = ["nope"]\n')

    with pytest.raises(ProfileError, match="someone/else"):
        load_profile("owner/repo", path)


def test_invalid_toml_is_refused(tmp_path):
    path = tmp_path / "verification.toml"
    path.write_text("[not toml")

    with pytest.raises(ProfileError):
        load_profile("owner/repo", path)


def test_the_shipped_profiles_are_valid_and_say_what_we_think_they_say():
    """profiles/verification.toml is versioned with the code and read on every
    run; a typo in it would surface as a gate that refuses to start."""
    assert PROFILES_PATH.exists()

    news_watch = load_profile("cgranetgithub/news-watch")
    biz_explore = load_profile("cgranetgithub/biz-explore")

    # Its own suite imports `anthropic`, which lives in the optional `audit` extra.
    assert news_watch.extras == ["test", "audit"]
    # It declares no interpreter at all; its CI installs 3.14.
    assert biz_explore.python_version == "3.14"
    # Neither entry narrows the gate: what the tools find, they report.
    assert news_watch.skipped_tools == []
    assert biz_explore.skipped_tools == []
