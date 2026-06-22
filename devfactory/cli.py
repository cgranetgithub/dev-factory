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


@app.command()
def run(
    issue: int = typer.Option(..., "--issue", "-i", help="GitHub issue number"),
    repo: str = typer.Option(..., "--repo", "-r", help="GitHub repo (owner/repo)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Process a single GitHub issue through the full pipeline."""
    from devfactory.logging_setup import setup_logging

    setup_logging(level="DEBUG" if verbose else "INFO", issue_number=issue)

    from devfactory.github.issues import fetch_issue
    from devfactory.orchestrator import Pipeline

    console.print(f"[bold blue]DevFactory[/] processing issue #{issue} on {repo}")

    gh_issue = fetch_issue(repo, issue)
    pipeline = Pipeline()
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
    sync: bool = typer.Option(False, "--sync", help="Sync registry with live Ollama models"),
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
        _sync_models(available)

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


def _sync_models(available: set[str]):
    """Register all Ollama models in the DB."""
    from devfactory.kb.database import db

    for name in available:
        db.upsert_model(name)
    console.print(f"[green]Synced {len(available)} model(s) to DB[/]")


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

    # 3. Ollama check
    console.print("\n[bold]3. Checking Ollama...[/]")
    try:
        from devfactory.models.client import ollama

        available = ollama.list_models()
        console.print(f"   [green]✓ Ollama running — {len(available)} model(s) available[/]")
        if not available:
            console.print("   [yellow]  No models pulled yet. Run: ollama pull qwen2.5-coder:7b[/]")
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
