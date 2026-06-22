"""
GitHub poller — continuously polls for issues labeled 'ready-for-dev'.
Processes one issue at a time (sequential).
"""

from __future__ import annotations

import logging
import time

from rich.console import Console

from devfactory.config import settings
from devfactory.github.issues import (
    fetch_ready_issues,
    mark_error,
    mark_in_progress,
    mark_ready_for_review,
)
from devfactory.kb.database import db

logger = logging.getLogger(__name__)
console = Console()


class Poller:
    def __init__(self, repo: str, interval: int | None = None):
        self.repo = repo
        self.interval = interval or settings.poll_interval

    def start(self):
        """Blocking poll loop — runs until interrupted."""
        console.print(f"[bold blue]DevFactory poller[/] watching [cyan]{self.repo}[/]")
        console.print(f"Polling every {self.interval}s for label [yellow]ready-for-dev[/]\n")

        while True:
            try:
                self._tick()
            except KeyboardInterrupt:
                console.print("\n[yellow]Poller stopped.[/]")
                break
            except Exception as e:
                logger.error(f"[poller] unexpected error: {e}", exc_info=True)

            time.sleep(self.interval)

    def _tick(self):
        issues = fetch_ready_issues(self.repo)

        if not issues:
            logger.debug(f"[poller] no issues ready in {self.repo}")
            return

        # Process the oldest issue first
        issue = issues[0]

        # Skip if already tracked in DB as in-progress or done
        if self._is_already_tracked(issue.number):
            logger.debug(f"[poller] issue #{issue.number} already tracked, skipping")
            return

        console.print(f"\n[bold green]→ Processing issue #{issue.number}:[/] {issue.title}")

        # Mark on GitHub immediately to prevent double-pickup
        mark_in_progress(self.repo, issue.number)

        from devfactory.orchestrator import Pipeline

        pipeline = Pipeline()

        try:
            ctx = pipeline.run(issue)
            if ctx.pr_url:
                mark_ready_for_review(self.repo, issue.number, ctx.pr_url)
                console.print(f"[bold green]✓ PR ready:[/] {ctx.pr_url}")
            else:
                logger.warning(f"[poller] pipeline done for #{issue.number} but no PR URL")
        except Exception as e:
            mark_error(self.repo, issue.number, str(e))
            console.print(f"[bold red]✗ Error on #{issue.number}:[/] {e}")

    def _is_already_tracked(self, issue_number: int) -> bool:
        """Check if this issue is already in the DB with a non-error status."""
        with db._conn() as conn:
            row = conn.execute(
                "SELECT status FROM tasks"
                " WHERE github_issue_id=? AND repo=? ORDER BY id DESC LIMIT 1",
                (issue_number, self.repo),
            ).fetchone()
        if row is None:
            return False
        return row["status"] in ("in_progress", "ready_for_merge", "merged")
