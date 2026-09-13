"""
What the gate has to build before it can verify a target repository.

The verification image ships **uv** and no fixed Python (see
``docker/Dockerfile.test``), so the interpreter and the install command are
properties of the *target*, read from the target. This module does that reading,
on the host, before any container starts: given a checkout and its
:class:`~devfactory.verification.profiles.VerificationProfile`, it resolves

  * which Python version the repository is verified with, and where that came from;
  * which commands install it, and which dependency shape they came from;
  * which optional-dependency groups are part of that install.

Resolving here rather than in a shell script inside the image is deliberate. The
decision is then testable without Docker, and — more importantly — recordable: a
run's evidence should say *3.12, from requires-python* rather than leave a reader
to re-derive it from a container's output.
"""

from __future__ import annotations

import logging
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from devfactory.verification.profiles import VerificationProfile

logger = logging.getLogger(__name__)

# The interpreter a repository that declares nothing is verified with. It has to
# be *some* version; this one is old enough that a target written for an earlier
# release still installs, and new enough to be the floor of what the ecosystem
# publishes wheels for. A target that needs another version says so in
# `requires-python`, in a `.python-version` file, or in its profile — and its CI
# is the tie-breaker, which is why biz-explore carries a profile entry.
DEFAULT_PYTHON_VERSION = "3.12"

# The versions docker/Dockerfile.test caches. A target asking for one of these
# pays nothing at prepare time; one asking for anything else pays a download per
# container, which works but is worth knowing about.
CACHED_PYTHON_VERSIONS = ("3.11", "3.12", "3.13", "3.14")

# Optional-dependency groups installed without being asked for. These names mean
# "the dependencies needed to test this project" by convention across the
# ecosystem, and the gate needs exactly that. Any other extra — an optional
# integration, a cloud backend — is installed only when a profile names it.
DERIVED_EXTRAS = ("dev", "test", "tests", "testing")

# Extra requirement files installed alongside requirements.txt when present. Same
# convention argument: these hold the test dependencies of a project that has no
# pyproject.toml to declare an extra in.
DEV_REQUIREMENTS_FILES = ("requirements-dev.txt", "requirements-test.txt")

# Where the copy of the checkout is installed, mirrored from the runner so the
# commands built here can name the virtualenv they create.
VENV_DIR = "/build/.venv"


@dataclass(frozen=True)
class TargetEnvironment:
    """The environment the gate builds for one target, and why.

    Attributes:
        python_version: The interpreter, as ``uv python install`` names it.
        python_source: Where that version came from — for the record, not for logic.
        install: Shell commands, in order, that install the target. They run in
            the copy of the checkout with ``VIRTUAL_ENV`` exported, after the
            virtualenv has been created.
        install_source: The dependency shape they were derived from.
        extras: Optional-dependency groups included in the install.
    """

    python_version: str
    python_source: str
    install: tuple[str, ...]
    install_source: str
    extras: tuple[str, ...]

    @property
    def commands(self) -> tuple[str, ...]:
        """Every command the prepare step runs, interpreter provisioning included.

        ``uv python install`` is not part of :attr:`install` because a profile
        overriding the install must not have to repeat it — the interpreter is
        provisioned the same way whatever installs on top of it, and the
        virtualenv has to exist before ``uv pip install`` can reach it.
        """
        return (
            f"uv python install {self.python_version}",
            f"uv venv --python {self.python_version} {VENV_DIR}",
            *self.install,
        )

    def describe(self) -> str:
        """One line for the log and the dry run: the decision, with its reasons."""
        extras = f", extras {list(self.extras)}" if self.extras else ""
        return (
            f"python {self.python_version} (from {self.python_source}), "
            f"install from {self.install_source}{extras}"
        )


def resolve_environment(repo_path: Path, profile: VerificationProfile) -> TargetEnvironment:
    """Read ``repo_path`` and decide how to build its environment.

    Nothing here executes: it inspects files and returns commands for the
    container to run, so the same decision can be asserted in a unit test and
    printed in a dry run.

    Args:
        repo_path: The checkout, on the host.
        profile: The target's profile. Every field it sets wins over what the
            repository declares — a profile exists precisely for the cases where
            the repository is silent or wrong.
    """
    pyproject = _read_pyproject(repo_path)
    version, version_source = _python_version(repo_path, pyproject, profile)
    extras = _extras(pyproject, profile)
    install, install_source = _install(repo_path, pyproject, profile, version, extras)
    env = TargetEnvironment(
        python_version=version,
        python_source=version_source,
        install=install,
        install_source=install_source,
        extras=extras,
    )
    logger.info(f"[verification] target environment: {env.describe()}")
    return env


def _read_pyproject(repo_path: Path) -> dict:
    """The target's ``pyproject.toml``, or an empty mapping.

    A malformed one is reported and treated as absent rather than raised: the
    install will then fail inside the container, where both mypy and pytest end
    in the error state with the output that explains it — which is the gate's
    existing way of saying "this repository could not be prepared".
    """
    path = repo_path / "pyproject.toml"
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning(f"[verification] {path} could not be read ({exc}); ignoring it")
        return {}


def _python_version(
    repo_path: Path, pyproject: dict, profile: VerificationProfile
) -> tuple[str, str]:
    """The interpreter to verify with, and where the answer came from."""
    if profile.python_version:
        return profile.python_version, "the profile"

    # uv's own convention, and free to honour: a repository that pins its
    # interpreter here has already answered the question.
    pinned = repo_path / ".python-version"
    if pinned.is_file():
        line = pinned.read_text(encoding="utf-8").strip().splitlines()
        if line and line[0].strip():
            return line[0].strip(), ".python-version"

    requires = pyproject.get("project", {}).get("requires-python")
    if isinstance(requires, str) and requires.strip():
        derived = _from_requires_python(requires)
        if derived:
            return derived, f"requires-python {requires}"

    return DEFAULT_PYTHON_VERSION, "the documented default"


def _from_requires_python(requires: str) -> str | None:
    """The lowest version satisfying ``requires``, e.g. ``>=3.12,<4`` → ``3.12``.

    The lowest and not the highest: it is the version the repository claims as its
    floor, the one its own resolver picks by default, and the one most likely to
    expose a dependency the target forgot to pin for a newer release.
    """
    try:
        spec = SpecifierSet(requires)
    except InvalidSpecifier:
        logger.warning(f"[verification] requires-python {requires!r} is not a valid specifier")
        return None

    for version in CACHED_PYTHON_VERSIONS:
        # `.0` because a specifier is compared against a release, not a series:
        # SpecifierSet(">=3.12").contains("3.12") is true, but ">3.11" needs a
        # patch component to answer at all.
        if spec.contains(f"{version}.0"):
            return version

    # Nothing the image caches satisfies it — a target ahead of us, or behind.
    # Its own declared floor is then the honest answer, and uv fetches it.
    floors = []
    for clause in spec:
        if clause.operator in (">=", ">", "==", "~=", "==="):
            try:
                floors.append(Version(clause.version.rstrip(".*")))
            except InvalidVersion:
                continue
    if not floors:
        logger.warning(f"[verification] requires-python {requires!r} declares no usable floor")
        return None
    floor = max(floors)
    logger.warning(
        f"[verification] requires-python {requires!r} needs {floor.major}.{floor.minor}, "
        "which the verification image does not cache; it will be downloaded per run"
    )
    return f"{floor.major}.{floor.minor}"


def _extras(pyproject: dict, profile: VerificationProfile) -> tuple[str, ...]:
    """Optional-dependency groups to install."""
    if profile.extras is not None:
        return tuple(profile.extras)
    declared = pyproject.get("project", {}).get("optional-dependencies", {})
    if not isinstance(declared, dict):
        return ()
    return tuple(name for name in DERIVED_EXTRAS if name in declared)


def _install(
    repo_path: Path,
    pyproject: dict,
    profile: VerificationProfile,
    version: str,
    extras: tuple[str, ...],
) -> tuple[tuple[str, ...], str]:
    """The commands that install the target, and the shape they came from.

    The three shapes in order of how much they tell us:

    1. a ``uv.lock`` — the target has resolved its dependencies itself, and
       ``uv sync --frozen`` installs exactly that resolution. Refusing to
       re-resolve is the point: a lockfile that no longer matches its
       ``pyproject.toml`` is a fact about the target, and the gate should report
       it rather than paper over it;
    2. a ``pyproject.toml`` — installed editable, so the checkout stays the only
       copy of the code. A second copy in ``site-packages`` is what makes mypy
       report a duplicate module instead of checking (issue #77), and a ``src``
       layout hits that immediately;
    3. ``requirements.txt`` — plus the conventional dev/test requirement files
       when they exist, because a suite cannot run without its test dependencies.

    A repository with none of the three installs nothing; the virtualenv still
    exists, and the gate still runs over whatever the standard library provides.
    """
    if profile.install is not None:
        return tuple(profile.install), "the profile"

    extras_flags = " ".join(f"--extra {shlex.quote(name)}" for name in extras)

    if (repo_path / "uv.lock").is_file():
        command = f"uv sync --frozen --python {version}"
        return ((f"{command} {extras_flags}".strip(),), "uv.lock")

    # The file, not the parsed table: a pyproject.toml we could not read is still
    # the shape this repository declares, and the install failing on it is the
    # report we want — not a silent fallback to "nothing declared".
    if (repo_path / "pyproject.toml").is_file() or (repo_path / "setup.py").is_file():
        target = f".[{','.join(extras)}]" if extras else "."
        return ((f"uv pip install -e {shlex.quote(target)}",), "pyproject.toml")

    if (repo_path / "requirements.txt").is_file():
        files = ["requirements.txt"]
        files += [name for name in DEV_REQUIREMENTS_FILES if (repo_path / name).is_file()]
        args = " ".join(f"-r {shlex.quote(name)}" for name in files)
        return ((f"uv pip install {args}",), "requirements.txt")

    logger.warning(
        "[verification] the target declares no dependencies (no uv.lock, "
        "pyproject.toml or requirements.txt); nothing will be installed"
    )
    return ((), "nothing declared")
