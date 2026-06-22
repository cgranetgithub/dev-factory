"""
DevFactory knowledge base — SQLite persistence layer.

Schema:
    models      — registered LLM models (name, parameters, provider).
    tasks       — one row per processed GitHub issue.
    executions  — one row per agent×model run within a task.
    scores      — quality metrics attached to each execution.

Use the ``db`` singleton for all access::

    from devfactory.kb.database import db
    task_id = db.create_task(issue_number=42, repo="owner/repo")
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from devfactory.config import settings

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT UNIQUE NOT NULL,
    parameters_b REAL,
    provider     TEXT DEFAULT 'ollama',
    notes        TEXT,
    added_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    github_issue_id INTEGER NOT NULL,
    repo            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    -- status: pending | in_progress | qa_failed | review_failed
    --         | ready_for_merge | merged | error
    branch_name     TEXT,
    pr_url          TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS executions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id           INTEGER REFERENCES tasks(id),
    model_id          INTEGER REFERENCES models(id),
    agent_type        TEXT NOT NULL,
    -- agent_type: analyst | developer | qa | reviewer
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    duration_ms       INTEGER DEFAULT 0,
    created_at        TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scores (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id INTEGER REFERENCES executions(id),
    metric       TEXT NOT NULL,
    -- metric: tests_pass_rate | lint_score | security_score
    --         | review_verdict | review_quality | retry_count
    value        REAL NOT NULL,
    notes        TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_executions_task   ON executions(task_id);
CREATE INDEX IF NOT EXISTS idx_executions_model  ON executions(model_id);
CREATE INDEX IF NOT EXISTS idx_scores_execution  ON scores(execution_id);
CREATE INDEX IF NOT EXISTS idx_tasks_issue       ON tasks(github_issue_id);
"""

# Valid task statuses — enforced at application level
TASK_STATUSES = frozenset(
    {
        "pending",
        "in_progress",
        "qa_failed",
        "review_failed",
        "ready_for_merge",
        "merged",
        "error",
    }
)


class Database:
    """SQLite-backed knowledge base for DevFactory."""

    def __init__(self, path: Path | None = None):
        self.path = path or settings.db_path
        self._ensure_db()

    def _ensure_db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)
        logger.debug(f"DB ready at {self.path}")

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Models ─────────────────────────────────────────────────────────────────

    def upsert_model(self, name: str, parameters_b: float | None = None, notes: str = "") -> int:
        """Insert a model if it doesn't exist; update notes if it does. Returns row id."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO models (name, parameters_b, notes)
                   VALUES (?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET notes=excluded.notes""",
                (name, parameters_b, notes),
            )
            row = conn.execute("SELECT id FROM models WHERE name=?", (name,)).fetchone()
            return row["id"]

    def get_model_id(self, name: str) -> int | None:
        """Return the DB id for a model name, or None if not registered."""
        with self._conn() as conn:
            row = conn.execute("SELECT id FROM models WHERE name=?", (name,)).fetchone()
            return row["id"] if row else None

    # ── Tasks ──────────────────────────────────────────────────────────────────

    def create_task(self, github_issue_id: int, repo: str) -> int:
        """Create a new task row and return its id."""
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO tasks (github_issue_id, repo) VALUES (?, ?)",
                (github_issue_id, repo),
            )
            return cur.lastrowid

    def update_task(self, task_id: int, **kwargs):
        """
        Update task fields by keyword argument.

        Allowed fields: status, branch_name, pr_url, completed_at.
        Unknown fields are silently ignored.
        Status values are validated against TASK_STATUSES.
        """
        allowed = {"status", "branch_name", "pr_url", "completed_at"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        if "status" in fields and fields["status"] not in TASK_STATUSES:
            raise ValueError(f"Invalid task status: {fields['status']!r}")
        set_clause = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE tasks SET {set_clause} WHERE id=?",
                (*fields.values(), task_id),
            )

    # ── Executions ─────────────────────────────────────────────────────────────

    def record_execution(
        self,
        task_id: int,
        model_name: str,
        agent_type: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        duration_ms: int = 0,
    ) -> int:
        """Record one agent×model execution and return its id."""
        model_id = self.get_model_id(model_name)
        if model_id is None:
            model_id = self.upsert_model(model_name)

        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO executions
                   (task_id, model_id, agent_type, prompt_tokens, completion_tokens, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (task_id, model_id, agent_type, prompt_tokens, completion_tokens, duration_ms),
            )
            return cur.lastrowid

    # ── Scores ─────────────────────────────────────────────────────────────────

    def record_score(self, execution_id: int, metric: str, value: float, notes: str = ""):
        """Attach a quality metric score to an execution."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO scores (execution_id, metric, value, notes) VALUES (?, ?, ?, ?)",
                (execution_id, metric, value, notes),
            )

    # ── Stats queries ──────────────────────────────────────────────────────────

    def model_stats(self) -> list[dict]:
        """Aggregate scores per model per agent_type, ordered by model and role."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT
                    m.name             AS model,
                    e.agent_type       AS role,
                    COUNT(e.id)        AS runs,
                    AVG(e.duration_ms) AS avg_ms,
                    s.metric,
                    AVG(s.value)       AS avg_score
                FROM executions e
                JOIN models m ON m.id = e.model_id
                LEFT JOIN scores s ON s.execution_id = e.id
                GROUP BY m.name, e.agent_type, s.metric
                ORDER BY m.name, e.agent_type, s.metric
            """).fetchall()
            return [dict(r) for r in rows]

    def task_counts(self) -> dict[str, int]:
        """Return a dict of {status: count} for all tasks."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"
            ).fetchall()
            return {r["status"]: r["cnt"] for r in rows}

    def recent_tasks(self, limit: int = 10) -> list[dict]:
        """Return the most recent tasks with their details."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, github_issue_id, repo, status,
                       branch_name, pr_url, created_at, completed_at
                FROM tasks
                ORDER BY id DESC
                LIMIT ?
            """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]


# Singleton — import this everywhere
db = Database()
