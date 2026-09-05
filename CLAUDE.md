# DevFactory — Claude Code Instructions

## Project overview

DevFactory is a local AI-powered software factory that processes GitHub issues through a
sequential SDLC pipeline: Analyst → Developer → Verification → Review → PR.
It runs entirely on local LLMs served by Ollama.

**Direction — read [`docs/VISION.md`](docs/VISION.md) before proposing architecture.**
The goal is not just automation: it is an issue → PR factory whose SDLC is precise enough
to be audited (SOC 2 / ISO 27001 first, IEC 62304 / ISO 13485 next). Two consequences for
day-to-day work:

- **Everything must stay local.** No pipeline step may send code, prompts or diffs to a
  third-party service unless it is an explicit, opt-in, documented backend.
- **Every step should leave evidence.** Prefer designs that record what happened
  (inputs, outputs, verdicts, timestamps, git SHAs) over designs that only produce a result.
  Never silently drop or overwrite an execution record.
- The reviewer's inability to approve its own PR is a **separation-of-duties control**,
  not a bug to work around.

## Repository layout

```
devfactory/
├── devfactory/          # Main package
│   ├── agents/          # Agent implementations (analyst, developer, qa, reviewer)
│   ├── qa/              # Docker verification runner (ruff, mypy, bandit, pytest)
│   ├── github/          # GitHub integration (poller, git_ops, pr, review, issues)
│   ├── kb/              # Knowledge base (SQLite, scorer, dashboard)
│   ├── models/          # LLM client, router, registry, retry
│   ├── config.py        # Settings (pydantic-settings, reads .env)
│   ├── context.py       # PipelineContext dataclass — shared state between agents
│   ├── orchestrator.py  # Sequential pipeline runner
│   ├── repo_context.py  # Reads workspace repo files for developer context
│   ├── logging_setup.py # Rich console + JSON-lines file logging
│   └── cli.py           # Typer CLI entry point
├── prompts/             # Prompt templates (Markdown, loaded at runtime)
├── docker/              # Dockerfile.test for verification isolation
└── tests/               # Pytest unit tests
```

## Key conventions

- **Python 3.11+** with type hints everywhere. Use `from __future__ import annotations`.
- **Pydantic v2** for data validation; `pydantic-settings` for config.
- **No heavy frameworks**: no LangGraph, no CrewAI. The pipeline is plain Python.
- **One singleton per subsystem**: `settings`, `ollama`, `router`, `db`, `scorer`, `gh`.
  Import the singleton, never instantiate manually (except in tests).
- **Agents are injectable**: `BaseAgent.__init__` accepts an optional forced `ModelMeta`.
  `Scorer.__init__` accepts an optional `Database` — use this in tests.
- **Prompts live in `prompts/`**: loaded via `BaseAgent.load_prompt()`. Never inline prompts in code.
- **All git operations** go through `devfactory.github.git_ops`.
  Never call `subprocess` for git — use GitPython.
- **All GitHub API calls** go through `devfactory.github.client.gh` (lazy singleton).

## Running tests

```bash
python -m pytest tests/ -v
```

All tests must pass before committing. Tests that require Ollama or GitHub are integration
tests (not included yet) — unit tests mock those boundaries.

## Adding a new agent

1. Create `devfactory/agents/my_agent.py` inheriting from `BaseAgent`.
2. Set `role = "my_role"` (must match a role in `models/registry.py`).
3. Implement `run(ctx: PipelineContext) -> PipelineContext`.
4. Add the role to relevant `ModelMeta.roles` entries in `models/registry.py`.
5. Create `prompts/my_role.md`.
6. Wire it into `orchestrator.py`.

## Adding a new model

Edit `devfactory/models/registry.py` and add a `ModelMeta` entry.
Then pull the model in Ollama: `ollama pull <model-name>`.

## Environment

Copy `.env.example` to `.env` and fill in:
- `GITHUB_TOKEN` — personal access token with `repo` and `pull_request` scopes.
- `GITHUB_USERNAME` — your GitHub username.
- Optionally adjust `OLLAMA_BASE_URL`, `DEVFACTORY_WORKSPACE`, etc.

## First-time setup

```bash
devfactory init --repo owner/repo
```

This creates GitHub labels, builds the Docker test image, and checks Ollama.

## Pipeline flow (for debugging)

```
Poller detects label ready-for-dev
  → mark_in_progress (label swap on GitHub)
  → Pipeline.run(issue)
      1. AnalystAgent   → TaskSpec (JSON)
      2. git_ops.setup_branch
      3. loop (shared budget: DEVFACTORY_MAX_VERIFICATION_RETRIES iterations):
           DeveloperAgent → writes files to workspace
           autofix (ruff --fix + format on the touched files)
           git_ops.commit_changes
           gate 1 — VerificationRunner (Docker) → VerificationReport
             if it fails: back to the developer
           gate 2 — ReviewerAgent → verdict on the diff vs the acceptance criteria
             if changes_requested: back to the developer
           both satisfied: break
      4. git_ops.push_branch
      5. create_or_update_pr
      6. post the review that governed the accepted iteration onto the PR
      7. scorer.flush → SQLite KB
  → mark_ready_for_review (issue comment + label)
```

## Style guide

- **Language: everything in English** — code, identifiers, comments, docstrings,
  commit messages, and docs. This project is intended to be open-sourced, so it
  does **not** follow the French-comments convention from the shared
  `/home/charles/Projects/CLAUDE.md`; that rule is overridden here.
- Max line length: **100 characters** (ruff enforced).
- Imports: stdlib → third-party → devfactory (ruff isort enforced).
- Logging: always use `logger = logging.getLogger(__name__)`, never `print()`.
- Exception handling: catch specific exceptions. No bare `except:` or `except Exception:`
  without a logged message and a good reason.
- Docstrings: module-level + public classes + public methods. Google style.
