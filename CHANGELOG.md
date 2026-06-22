# Changelog

All notable changes to DevFactory are documented here.
This project follows [Semantic Versioning](https://semver.org/).

---

## [0.1.0] — 2024-06-22

### Initial release

**Core pipeline**
- Sequential SDLC agent pipeline: Analyst → Developer → QA → Reviewer × 2
- `PipelineContext` dataclass as shared state passed between all agents
- Developer → QA retry loop with configurable max attempts
- Per-run JSON-lines log files under `logs/`

**Agents**
- `AnalystAgent` — parses GitHub issue into a structured `TaskSpec` (JSON)
- `DeveloperAgent` — generates code from `TaskSpec`, injects repo file tree and existing file
  contents for context-aware modifications
- `QAAgent` — orchestrates Docker-based QA (ruff + mypy + bandit + pytest)
- `ReviewerAgent` — posts real GitHub PR reviews with inline diff comments

**Model layer**
- `OllamaClient` — thin wrapper over the Ollama `/api/chat` endpoint with retry logic
- `ModelRouter` — random model selection per agent role, with Ollama availability check
- `ModelRegistry` — declarative model catalogue (`models/registry.py`)

**GitHub integration**
- Issue poller watching for the `ready-for-dev` label
- Full git workflow via GitPython: clone, branch, commit, push (`--force-with-lease`)
- PR creation with structured body: acceptance criteria checkboxes, QA summary, model assignments
- Inline GitHub PR Reviews with diff-position mapping
- Automated label management: `devfactory:in-progress`, `devfactory:ready-for-review`,
  `devfactory:error`

**Knowledge base**
- SQLite schema: `models`, `tasks`, `executions`, `scores`
- Automatic scoring after each pipeline run (tests pass rate, lint score, security, review verdict)
- Rich terminal dashboard: leaderboard, per-role metric breakdown, task status summary
- `devfactory stats` and `devfactory logs` CLI commands

**Developer experience**
- `devfactory init` — one-command setup (labels, Docker image, Ollama check, DB)
- `devfactory models --sync` — sync Ollama models into the KB
- 27 unit tests, zero external dependencies required for test suite
