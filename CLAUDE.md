# DevFactory — Claude Code Instructions

## Project overview

DevFactory is a local AI-powered software factory that processes GitHub issues through a
sequential SDLC pipeline: Analyst → Developer → Verification → Review → PR.
It runs entirely on local LLMs served by Ollama.

**Direction — read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for where the
pipeline is going, and [`docs/VISION.md`](docs/VISION.md) for why.**
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
│   ├── agents/          # Agent implementations (analyst, developer, verification, reviewer)
│   ├── verification/    # Scope gate, ruff autofix, Docker runner (ruff/mypy/bandit/pytest)
│   ├── github/          # GitHub integration (poller, git_ops, pr, review, issues)
│   ├── kb/              # Knowledge base (SQLite, scorer, dashboard)
│   ├── models/          # Ollama client, router, registry, provisioning
│   ├── config.py        # Settings (pydantic-settings, reads .env)
│   ├── context.py       # PipelineContext — orchestration state (see ARCHITECTURE.md)
│   ├── orchestrator.py  # Pipeline stages and the graph nodes
│   ├── graph.py         # The developer → gates flow (LangGraph)
│   ├── opencode.py      # The harness every agent runs through
│   ├── logging_setup.py # Rich console + JSON-lines file logging
│   └── cli.py           # Typer CLI entry point
├── prompts/             # Prompt templates (Markdown, loaded at runtime)
├── docker/              # Dockerfile.test for verification isolation
└── tests/               # Pytest unit tests
```

## Key conventions

- **Python 3.11+** with type hints everywhere. Use `from __future__ import annotations`.
- **Pydantic v2** for data validation; `pydantic-settings` for config.
- **Never reinvent the wheel**: if a maintained package does what we need, use it.
  This replaces the earlier rule forbidding orchestration frameworks, which was
  written when the pipeline was a straight line. It now has cycles — the reviewer
  sends the change back, verification and review run again — so LangGraph is being
  adopted for the flow. See `docs/ARCHITECTURE.md`.
- **Agents are autonomous**: an agent does not need to know what the previous one
  did, only that it is its turn. Agents pass nothing to each other; their outputs
  are stored where the next stage can find them (the issue, a linked spec issue,
  the codebase). Do not add a field to a shared object to carry information
  between stages.
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
3. Implement `run(ctx: PipelineContext) -> PipelineContext`, reaching the model
   through `opencode.run(...)` — every agent works in the checkout.
   Pass `read_only=True` unless the agent's job is to change the code.
4. Add the role to relevant `ModelMeta.roles` entries in `models/registry.py`.
   Only models with `drives_agentic_loop=True` can hold an agentic role.
5. Create `prompts/my_role.md`.
6. Wire it into `orchestrator.py`, and into `graph.py` if it is a gate.

An agent must be able to do its job from the issue, the published artifacts and
the codebase — see the autonomy rule above. If it needs something the previous
stage held in memory, publish that thing instead of passing it.

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
  → Pipeline.run(issue)
      1. git_ops.setup_branch          — the analyst needs a checkout to read
      2. AnalystAgent (OpenCode, read-only)
           reads the request AND the codebase
           publishes the spec as a linked issue, labelled devfactory:spec
      3. graph (shared budget: DEVFACTORY_MAX_VERIFICATION_RETRIES iterations):
           DeveloperAgent (OpenCode) → edits the workspace
           autofix (ruff --fix + format, on the touched files only)
           git_ops.commit_changes
           gate 0 — scope        : did it touch the files the task declared?
           gate 1 — verification : ruff, mypy, bandit, pytest, in Docker
           gate 2 — review       : OpenCode read-only, judges against the spec
           any gate can send the change back; all three share one budget
      4. git_ops.push_branch
      5. create_or_update_pr           — closes the issue and its spec issue
      6. post the review that governed the accepted iteration
      7. scorer.flush → SQLite KB
  → the pipeline applies the issue's status labels itself, whatever the outcome
```

The flow is a LangGraph graph (`devfactory/graph.py`); the run is checkpointed, and
`devfactory run --resume <thread-id>` continues an interrupted one.

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
