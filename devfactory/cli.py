"""
DevFactory CLI — entry point for all commands.
Usage: devfactory <command> [options]
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="devfactory", help="Local AI software factory")
controls_app = typer.Typer(help="Verify the repository controls and record the evidence")
app.add_typer(controls_app, name="controls")
gate_app = typer.Typer(help="Run the verification gate outside a pipeline run")
app.add_typer(gate_app, name="gate")
console = Console()


# Declared here rather than inline: ruff B008 forbids a call in an argument default.
_MODEL_OPTION = typer.Option(
    [],
    "--model",
    "-m",
    help="Pin a role to a model, as role=name (e.g. -m developer=gemma4:26b). "
    "Repeatable. Without it each role is drawn at random, which makes two runs "
    "incomparable.",
)


_RESUME_OPTION = typer.Option(
    "",
    "--resume",
    help="Continue an interrupted run from its last checkpoint. The thread id is "
    "printed at the start of every run.",
)


@app.command()
def run(
    issue: int = typer.Option(..., "--issue", "-i", help="GitHub issue number"),
    repo: str = typer.Option(..., "--repo", "-r", help="GitHub repo (owner/repo)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    model: list[str] = _MODEL_OPTION,
    resume: str = _RESUME_OPTION,
):
    """Process a single GitHub issue through the full pipeline."""
    from devfactory.logging_setup import setup_logging

    setup_logging(level="DEBUG" if verbose else "INFO", issue_number=issue)

    from devfactory.github.issues import fetch_issue
    from devfactory.orchestrator import Pipeline

    overrides = {}
    for pair in model:
        if "=" not in pair:
            console.print(f"[bold red]✗[/] --model expects role=name, got '{pair}'")
            raise typer.Exit(code=2)
        role, name = pair.split("=", 1)
        overrides[role.strip()] = name.strip()

    console.print(f"[bold blue]DevFactory[/] processing issue #{issue} on {repo}")

    gh_issue = fetch_issue(repo, issue)
    try:
        pipeline = Pipeline(model_overrides=overrides, resume_thread=resume or None)
    except ValueError as e:
        console.print(f"[bold red]✗[/] {e}")
        raise typer.Exit(code=2) from e
    try:
        ctx = pipeline.run(gh_issue)
    except Exception as e:
        # The pipeline has already logged the traceback and labelled the issue; the
        # only thing missing was telling the caller. A run that died on a harness
        # hiccup used to exit 0, which makes `devfactory run` impossible to script:
        # the shell saw success and the next command ran on a branch with no PR.
        console.print(f"[bold red]✗ Pipeline failed:[/] {e}")
        raise typer.Exit(code=1) from e

    if ctx.pr_url:
        console.print(f"[bold green]✓ Done![/] PR: {ctx.pr_url}")
    else:
        console.print("[bold yellow]⚠ Pipeline completed but no PR was created[/]")


@app.command()
def poll(
    repo: str = typer.Option(..., "--repo", "-r", help="GitHub repo (owner/repo)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Poll GitHub for issues labeled 'ready-for-dev' and process them."""
    from devfactory.logging_setup import setup_logging

    setup_logging(level="DEBUG" if verbose else "INFO")

    from devfactory.github.poller import Poller

    Poller(repo).start()


@app.command()
def stats(
    role: str = typer.Option("", "--role", help="Filter by agent role"),
    metric: str = typer.Option("", "--metric", help="Filter by metric"),
):
    """Show model performance statistics from the knowledge base."""
    from devfactory.kb.dashboard import print_dashboard
    from devfactory.kb.database import db

    print_dashboard(db, role_filter=role or None, metric_filter=metric or None)


@app.command()
def models(
    sync: bool = typer.Option(
        False, "--sync", help="Pull every registered model that is missing from Ollama"
    ),
):
    """List registered models and check Ollama availability."""
    from devfactory.models.client import ollama
    from devfactory.models.registry import MODELS

    try:
        available = set(ollama.list_models())
    except Exception:
        available = set()
        console.print("[yellow]⚠ Could not reach Ollama — showing registry only[/]")

    if sync:
        # The registry is the source of truth: pull whatever it declares but
        # Ollama does not yet have, then refresh the availability set.
        available = _sync_models(available)

    table = Table(title="Model Registry", show_lines=True)
    table.add_column("Model", style="cyan")
    table.add_column("Params")
    table.add_column("Ctx (K)")
    table.add_column("Roles")
    table.add_column("Ollama", justify="center")

    for m in MODELS:
        in_ollama = m.name in available
        status = "[green]✓[/]" if in_ollama else "[red]✗[/]"
        table.add_row(
            m.name,
            f"{m.parameters_b}B",
            str(m.context_k),
            ", ".join(m.roles),
            status,
        )

    console.print(table)

    not_registered = available - {m.name for m in MODELS}
    if not_registered:
        console.print(
            f"\n[dim]Ollama models not in registry: {', '.join(sorted(not_registered))}[/]"
        )
        console.print("[dim]Edit devfactory/models/registry.py to add them.[/]")


@app.command()
def init(
    repo: str = typer.Option(..., "--repo", "-r", help="GitHub repo (owner/repo)"),
):
    """Setup DevFactory for a repository, and check the repository is usable.

    Creates the GitHub labels, builds the verification image, provisions the
    models, initialises the knowledge base, and finishes with a dry run of the
    gate on the repository's default branch.

    Exit codes: 0 ready, 1 the target's own gate fails, 2 the gate could not run.
    Both non-zero cases mean the factory would fail at verification on every
    issue, so init refuses to call itself complete.
    """
    from devfactory.logging_setup import setup_logging

    setup_logging()
    raise typer.Exit(code=_run_init(repo))


@app.command()
def logs(
    issue: int = typer.Option(..., "--issue", "-i", help="Issue number to show logs for"),
    last: bool = typer.Option(True, "--last/--all", help="Show only the last run"),
):
    """Show logs for a pipeline run."""
    from devfactory.kb.dashboard import print_run_logs

    print_run_logs(issue_number=issue, last_only=last)


@controls_app.command("check")
def controls_check(
    repo: str = typer.Option(..., "--repo", "-r", help="GitHub repo (owner/repo)"),
    as_json: bool = typer.Option(
        False, "--json", help="Print the canonical snapshot and the drift as JSON"
    ),
):
    """Snapshot the enforced branch protections, record them, report any drift.

    Exit codes: 0 no drift, 1 drift detected, 2 the configuration could not be read.
    """
    import json

    from devfactory.controls import SnapshotError, check_repository, flatten, format_change

    try:
        check = check_repository(repo)
    except SnapshotError as exc:
        # Exit 2, not 1: "we could not look" must never be reported as "nothing moved",
        # and an unreadable API is not a drift finding.
        console.print(f"[bold red]✗ Could not read the controls:[/] {exc}")
        raise typer.Exit(code=2) from exc

    if as_json:
        console.print_json(
            json.dumps(
                {
                    "repo": check.repo,
                    "taken_at": check.taken_at,
                    "sha256": check.sha256,
                    "snapshot_id": check.snapshot_id,
                    "baseline": check.baseline,
                    "snapshot": check.snapshot,
                    "drift": check.changes,
                }
            )
        )
        raise typer.Exit(code=1 if check.drifted else 0)

    table = Table(title=f"Controls — {check.repo} @ {check.taken_at}", show_lines=False)
    # Settings are keyed by the same dotted paths the drift below uses, so a reader
    # can match a reported change to the line it came from without a translation.
    table.add_column("Setting", style="cyan", overflow="fold", ratio=3)
    table.add_column("Value", overflow="fold", ratio=2)
    for path, value in sorted(flatten(check.snapshot).items()):
        table.add_row(path, str(value))
    console.print(table)
    console.print(f"[dim]sha256 {check.sha256} · record #{check.snapshot_id}[/]")

    if check.baseline:
        console.print("[bold green]✓ Baseline recorded[/] — nothing to compare against yet")
        raise typer.Exit(code=0)

    if not check.changes:
        console.print(f"[bold green]✓ No drift[/] since {check.previous_taken_at}")
        raise typer.Exit(code=0)

    console.print(
        f"\n[bold yellow]⚠ {len(check.changes)} control change(s)[/] "
        f"since {check.previous_taken_at}:"
    )
    for change in check.changes:
        console.print(f"  [yellow]•[/] {format_change(change)}")
    raise typer.Exit(code=1)


@gate_app.command("check")
def gate_check(
    repo: str = typer.Option(..., "--repo", "-r", help="GitHub repo (owner/repo)"),
    path: str = typer.Option(
        "",
        "--path",
        help="Verify this checkout instead of cloning the repository. The "
        "--repo slug still selects the verification profile.",
    ),
):
    """Run the full gate on a repository's default branch, tool by tool.

    Onboarding check: a repository whose default branch fails its own gate cannot
    be processed by the factory, because every run would fail at verification
    whatever the developer produced.

    Exit codes: 0 the gate passes, 1 the target's own suite fails, 2 the gate
    itself could not run.
    """
    from devfactory.logging_setup import setup_logging

    setup_logging()
    raise typer.Exit(code=_gate_dry_run(repo, Path(path) if path else None))


def _gate_dry_run(repo: str, path: Path | None = None) -> int:
    """Run the gate dry run and print it. Returns the exit code it deserves.

    Shared by ``devfactory gate check`` and ``devfactory init``, which both need
    the same verdict printed the same way — one before onboarding, one as part of
    it.
    """
    from devfactory.verification.dry_run import dry_run

    console.print(f"\n[bold]Gate dry run on {repo}[/] (this builds the target's environment)")
    try:
        result = dry_run(repo, path)
    except (OSError, ValueError, RuntimeError) as exc:
        # Includes the clone failing, a path that is not a directory and a
        # malformed profile. None of them is a verdict on the repository.
        console.print(f"   [bold red]✗ The gate could not run:[/] {exc}")
        return 2

    table = Table(title=f"{repo} @ {result.commit[:8]} ({result.branch})", show_lines=False)
    table.add_column("Tool", style="cyan")
    table.add_column("Result")
    table.add_column("Detail", overflow="fold")
    for outcome in result.outcomes:
        marker = {
            "clean": "[green]✓ pass[/]",
            "findings": "[yellow]✗ findings[/]",
            "error": "[red]✗ did not run[/]",
            "skipped": "[dim]— skipped[/]",
        }[outcome.status]
        table.add_row(outcome.name, marker, outcome.detail)
    console.print(table)
    console.print(f"[dim]{result.environment.describe()} · {result.duration_s:.1f}s[/]")

    # The two failure modes are printed apart because the reader's next action is
    # not the same: one is a bug or a missing declaration on our side, the other
    # is work in the target repository.
    if result.gate_failures:
        names = ", ".join(o.name for o in result.gate_failures)
        console.print(f"\n[bold red]✗ The gate could not run ({names}).[/]")
        console.print("[dim]Our problem, or a declaration missing from the target:[/]")
        for outcome in result.gate_failures:
            console.print(f"  [red]•[/] {outcome.name}: {outcome.detail}")
        return 2

    if result.target_failures:
        names = ", ".join(o.name for o in result.target_failures)
        console.print(f"\n[bold yellow]✗ {repo} does not pass its own gate ({names}).[/]")
        console.print(
            "[dim]The target's problem: the factory cannot open a pull request on a "
            "branch whose base already fails. Fix it in the target, or record a "
            "per-repository decision in profiles/verification.toml — see "
            "docs/onboarding.md.[/]"
        )
        return 1

    console.print(f"\n[bold green]✓ {repo} passes its own gate.[/]")
    return 0


def _sync_models(available: set[str]) -> set[str]:
    """Pull every registered model that Ollama does not have yet.

    Thin wrapper over :func:`devfactory.models.provisioning.ensure_models_available`,
    which the pipeline also calls at the start of a run. Two implementations of
    "pull what the registry declares" would drift, and the one that drifted would
    be the one nobody ran that day.
    """
    from devfactory.models.client import ollama
    from devfactory.models.provisioning import ensure_models_available

    pulled = ensure_models_available()
    if not pulled:
        console.print("[green]✓ All registered models are already pulled in Ollama[/]")
        return available

    console.print(f"[green]✓ Pulled {len(pulled)} model(s): {', '.join(pulled)}[/]")
    try:
        return set(ollama.list_models())
    except (OSError, RuntimeError):
        return available | set(pulled)


def _run_init(repo: str) -> int:
    import subprocess

    from devfactory.github.issues import _ensure_labels

    console.rule("[bold blue]DevFactory Init[/]")

    # 1. GitHub labels
    console.print("\n[bold]1. Creating GitHub labels...[/]")
    try:
        _ensure_labels(repo)
        console.print("   [green]✓ Labels ready[/]")
    except Exception as e:
        console.print(f"   [red]✗ {e}[/]")

    # 2. Docker image
    console.print("\n[bold]2. Building Docker test image...[/]")
    result = subprocess.run(
        ["docker", "build", "-f", "docker/Dockerfile.test", "-t", "devfactory-test:latest", "."],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        console.print("   [green]✓ Image built: devfactory-test:latest[/]")
    else:
        console.print(f"   [red]✗ Docker build failed:[/]\n{result.stderr[-500:]}")

    # 3. Ollama check + provisioning
    console.print("\n[bold]3. Checking Ollama...[/]")
    try:
        from devfactory.models.client import ollama
        from devfactory.models.provisioning import check_ollama_version, ensure_models_available

        version = ollama.version()
        ok = check_ollama_version()
        marker = "[green]✓[/]" if ok else "[yellow]⚠[/]"
        console.print(f"   {marker} Ollama {version}")

        # Pull whatever the registry declares but Ollama lacks. Setup is the right
        # moment for a multi-GB download — not the middle of a pipeline run.
        console.print("   Provisioning registered models (this can take a while)…")
        pulled = ensure_models_available()
        if pulled:
            console.print(f"   [green]✓ Pulled {len(pulled)} model(s): {', '.join(pulled)}[/]")
        else:
            console.print("   [green]✓ All registered models available[/]")
    except Exception as e:
        console.print(f"   [red]✗ Ollama not reachable: {e}[/]")

    # 4. DB init
    console.print("\n[bold]4. Initialising database...[/]")
    from devfactory.kb.database import db

    db._ensure_db()
    console.print(f"   [green]✓ DB ready at {db.path}[/]")

    # 5. The gate, on the repository as it stands. Last because it needs the image
    # built above, and because it is the step that decides whether the other four
    # were worth doing: a repository whose default branch fails its own gate
    # cannot be processed at all (issue #92).
    console.print("\n[bold]5. Dry run of the verification gate on the default branch...[/]")
    code = _gate_dry_run(repo)
    if code != 0:
        console.print("\n[bold yellow]Init incomplete[/] — see docs/onboarding.md.")
        return code

    console.print("\n[bold green]Init complete.[/] Run:")
    console.print(f"   devfactory poll --repo {repo}")
    return 0


if __name__ == "__main__":
    app()
