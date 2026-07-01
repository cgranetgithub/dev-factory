"""
KB Dashboard — Rich terminal views for model performance stats and run logs.
"""

from __future__ import annotations

import json
from pathlib import Path

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from devfactory.kb.database import Database

console = Console()

METRICS_ORDER = [
    "tests_pass_rate",
    "lint_score",
    "security_score",
    "review_verdict",
    "review_quality",
]

# Metrics that are 0.0–1.0 quality scores (higher = better) and may be averaged
# into the leaderboard ranking. Diagnostic counts such as "retry_count" are
# recorded for the per-role breakdown but must NOT be averaged into the ranking,
# otherwise a perfect run (retry_count == 0) wrongly drags a model's score down.
SCORE_METRICS = frozenset(METRICS_ORDER)


def print_dashboard(db: Database, role_filter: str | None = None, metric_filter: str | None = None):
    """Full stats dashboard."""
    rows = db.model_stats()

    if not rows:
        console.print("[yellow]No data yet — run some pipelines first.[/]")
        return

    # ── Summary counts ─────────────────────────────────────────────────────────
    task_counts = db.task_counts()
    panels = []
    for status, count in task_counts.items():
        color = {"ready_for_merge": "green", "error": "red", "qa_failed": "yellow"}.get(
            status, "blue"
        )
        panels.append(Panel(f"[bold]{count}[/]", title=status, border_style=color, width=20))
    if panels:
        console.print(Columns(panels))
        console.print()

    # ── Leaderboard ────────────────────────────────────────────────────────────
    console.rule("[bold]Model Leaderboard[/]")
    leaderboard = _build_leaderboard(rows, role_filter, metric_filter)
    console.print(leaderboard)

    # ── Per-role breakdown ────────────────────────────────────────────────────
    console.rule("[bold]Per-Role Breakdown[/]")
    roles = sorted({r["role"] for r in rows if r["role"]})
    if role_filter:
        roles = [r for r in roles if r == role_filter]

    for role in roles:
        role_rows = [r for r in rows if r["role"] == role]
        if not role_rows:
            continue
        table = _build_role_table(role, role_rows, metric_filter)
        console.print(table)
        console.print()


def _rank_models(
    rows: list[dict], role_filter: str | None, metric_filter: str | None
) -> list[tuple[str, float, int]]:
    """Rank models by average quality score.

    Returns ``(model, avg_score, data_points)`` tuples, best first. Only 0.0–1.0
    quality metrics (``SCORE_METRICS``) are averaged; diagnostic counts such as
    ``retry_count`` are ignored so a clean run does not drag a model down.
    """
    scores: dict[str, list[float]] = {}
    for row in rows:
        if role_filter and row.get("role") != role_filter:
            continue
        if metric_filter and row.get("metric") != metric_filter:
            continue
        # Rank on quality scores only — skip diagnostic counts like retry_count.
        if row.get("metric") not in SCORE_METRICS:
            continue
        if row.get("avg_score") is not None:
            scores.setdefault(row["model"], []).append(row["avg_score"])

    return sorted(
        [(model, sum(vals) / len(vals), len(vals)) for model, vals in scores.items()],
        key=lambda x: -x[1],
    )


def _build_leaderboard(
    rows: list[dict], role_filter: str | None, metric_filter: str | None
) -> Table:
    """Overall model ranking by average quality score."""
    ranked = _rank_models(rows, role_filter, metric_filter)

    table = Table(box=box.ROUNDED, show_lines=False)
    table.add_column("#", style="dim", width=3)
    table.add_column("Model", style="cyan")
    table.add_column("Avg Score", justify="right")
    table.add_column("Data Points", justify="right")

    for i, (model, avg, count) in enumerate(ranked, 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
        color = "green" if avg >= 0.7 else "yellow" if avg >= 0.5 else "red"
        table.add_row(medal, model, f"[{color}]{avg:.2f}[/]", str(count))

    return table


def _build_role_table(role: str, rows: list[dict], metric_filter: str | None) -> Table:
    table = Table(title=f"[bold]{role}[/]", box=box.SIMPLE_HEAVY, show_lines=True)
    table.add_column("Model", style="cyan")
    table.add_column("Runs", justify="right")
    table.add_column("Avg ms", justify="right")

    metrics = sorted({r["metric"] for r in rows if r["metric"]})
    if metric_filter:
        metrics = [m for m in metrics if m == metric_filter]
    for m in metrics:
        table.add_column(m, justify="right")

    # Group by model
    by_model: dict[str, dict] = {}
    for row in rows:
        model = row["model"]
        if model not in by_model:
            by_model[model] = {"runs": row["runs"], "avg_ms": row["avg_ms"], "metrics": {}}
        if row.get("metric") and row.get("avg_score") is not None:
            by_model[model]["metrics"][row["metric"]] = row["avg_score"]

    for model, data in sorted(by_model.items()):
        metric_cols = []
        for m in metrics:
            val = data["metrics"].get(m)
            if val is None:
                metric_cols.append("[dim]—[/]")
            else:
                color = "green" if val >= 0.7 else "yellow" if val >= 0.5 else "red"
                metric_cols.append(f"[{color}]{val:.2f}[/]")

        avg_ms = data["avg_ms"]
        ms_str = f"{avg_ms:.0f}" if avg_ms else "—"
        table.add_row(model, str(data["runs"]), ms_str, *metric_cols)

    return table


def print_run_logs(issue_number: int, last_only: bool = True):
    """Print log file(s) for a given issue."""
    logs_dir = Path("./logs")
    pattern = f"issue-{issue_number}-*.log"
    log_files = sorted(logs_dir.glob(pattern))

    if not log_files:
        console.print(f"[yellow]No logs found for issue #{issue_number}[/]")
        return

    files_to_show = [log_files[-1]] if last_only else log_files

    for log_path in files_to_show:
        console.rule(f"[bold]{log_path.name}[/]")
        try:
            for line in log_path.read_text().splitlines():
                try:
                    entry = json.loads(line)
                    level = entry.get("level", "INFO")
                    color = {"ERROR": "red", "WARNING": "yellow", "DEBUG": "dim"}.get(
                        level, "white"
                    )
                    ts = entry.get("ts", "")[:19]
                    logger = entry.get("logger", "")
                    msg = entry.get("msg", "")
                    console.print(f"[dim]{ts}[/] [{color}]{level:7}[/] [cyan]{logger}[/]: {msg}")
                    if "exc" in entry:
                        console.print(f"[red]{entry['exc']}[/]")
                except json.JSONDecodeError:
                    console.print(line)
        except Exception as e:
            console.print(f"[red]Could not read log: {e}[/]")
