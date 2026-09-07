"""
DevFactory CLI — entry point for all commands.
Usage: devfactory <command> [options]
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="devfactory", help="Local AI software factory")
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


@app.command()
def run(
    issue: int = typer.Option(..., "--issue", "-i", help="GitHub issue number"),
    repo: str = typer.Option(..., "--repo", "-r", help="GitHub repo (owner/repo)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    model: list[str] = _MODEL_OPTION,
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
        pipeline = Pipeline(model_overrides=overrides)
    except ValueError as e:
        console.print(f"[bold red]✗[/] {e}")
        raise typer.Exit(code=2) from e
    ctx = pipeline.run(gh_issue)

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
    """Setup DevFactory: create GitHub labels, build Docker image, check Ollama."""
    from devfactory.logging_setup import setup_logging

    setup_logging()
    _run_init(repo)


@app.command()
def logs(
    issue: int = typer.Option(..., "--issue", "-i", help="Issue number to show logs for"),
    last: bool = typer.Option(True, "--last/--all", help="Show only the last run"),
):
    """Show logs for a pipeline run."""
    from devfactory.kb.dashboard import print_run_logs

    print_run_logs(issue_number=issue, last_only=last)


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


def _run_init(repo: str):
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

    console.print("\n[bold green]Init complete.[/] Run:")
    console.print(f"   devfactory poll --repo {repo}")


if __name__ == "__main__":
    app()
