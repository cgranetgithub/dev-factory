# DevFactory

> A local AI-powered software factory that turns GitHub Issues into Pull Requests —
> entirely on your own hardware.

DevFactory runs a sequential SDLC agent pipeline using small local LLMs (via Ollama).
It watches your GitHub repository for issues labelled `ready-for-dev`, processes them
one by one through a structured workflow, and opens a reviewed Pull Request — ready for
your final approval.

No cloud LLM costs. No data leaves your machine. Every model run is scored and stored
so you can compare models objectively over time.

The longer-term goal is an issue → PR factory whose SDLC is precise enough to be
**audited** — SOC 2 / ISO 27001 first, IEC 62304 / ISO 13485 next.
See [Vision & compliance](#vision--compliance).

---

## Table of contents

- [How it works](#how-it-works)
- [Vision & compliance](#vision--compliance)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [First-time setup](#first-time-setup)
- [Usage](#usage)
- [Adding models](#adding-models)
- [Knowledge base & scoring](#knowledge-base--scoring)
- [Project structure](#project-structure)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## How it works

```
You (with Claude Opus)          DevFactory (local)
─────────────────────           ──────────────────────────────────────
Create detailed GitHub Issue    Polls for label ready-for-dev
Label it ready-for-dev    ───►  AnalystAgent   → structured TaskSpec
                                DeveloperAgent → writes code (with repo context)
                                QARunner       → ruff + mypy + bandit + pytest
                                   ↑ retry up to N times if QA fails
                                ReviewerAgent  → inline GitHub PR review (model A)
                                ReviewerAgent  → inline GitHub PR review (model B)
                                Opens PR + notifies you
                                Scores each model → SQLite KB
You review & merge        ◄───  PR ready for your review
```

1. **You write the spec** — Create a rich GitHub issue (you can use Claude Opus for this).
   Add the `ready-for-dev` label when it's ready.

2. **DevFactory picks it up** — The poller detects the issue and starts the pipeline.

3. **Analyst reads the issue** — A local LLM produces a structured `TaskSpec`: files to
   create/modify, acceptance criteria, test strategy, technical notes.

4. **Developer writes the code** — Another local LLM generates the implementation.
   It reads the existing repo files to make context-aware changes.

5. **QA validates** — The code is tested in an isolated Docker container: linting (ruff),
   type checking (mypy), security scanning (bandit), and tests (pytest).
   If QA fails, the developer retries (up to `DEVFACTORY_MAX_QA_RETRIES` times) with
   the QA report as feedback.

6. **Two reviewers** — Two different models post inline code review comments on the PR
   using the GitHub Review API.

7. **You merge** — The PR is opened, the issue is notified, and you decide when to merge.
   All model performance data is recorded in the local SQLite knowledge base.

---

## Vision & compliance

DevFactory is built for a use case cloud agents cover badly: **regulated and IP-sensitive
software work**, where the code must stay on the premises *and* the whole chain must be
provable.

Two properties drive every design decision:

- **Local-first** — code, prompts and model weights never leave the machine.
- **Auditable** — every pipeline step leaves a stored, timestamped, linkable record.

The compliance targets are adopted in sequence:

| Lens | Standards | Governs | When |
|---|---|---|---|
| The factory | SOC 2 · ISO/IEC 27001 | Access control, audit logging, change management, data residency — is the *system that produces the software* run under control? | Now |
| The product | IEC 62304 · ISO 13485 · ISO 14971 | Requirement → test traceability, safety classification, risk management, Design History File — was *this software* engineered through a controlled process? | Next |

Both reduce to the same backbone — **traceability + immutable evidence + explicit
controls** — so the medical tier is an uplift on the infosec foundation, not a rebuild.

Parts of it already exist by accident of design: the staged pipeline, the SQLite execution
log, branch → PR → CODEOWNERS → protected `main`, and the fact that the reviewer agent
*cannot* approve its own PR (a genuine separation-of-duties control). What is missing is
formal gates, immutable evidence, and verified issue → requirement → code → test → review
links.

One principle bounds the autonomy: **an AI drafts, a competent human disposes.** No
standard asks who typed the code — they ask who is accountable and whether the process
caught the error. Autonomy scales down as safety class scales up.

📄 **Full architecture and phased roadmap (P0 → P3): [docs/VISION.md](docs/VISION.md).**

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  GitHub                                                         │
│  Issues (ready-for-dev) ──► Poller ──► Pipeline                │
│  PR Reviews ◄──────────────────────── Reviewer × 2             │
└─────────────────────────────────────────────────────────────────┘
                               │
                    PipelineContext (shared state)
                               │
        ┌──────────┬───────────┼──────────┬────────────┐
        ▼          ▼           ▼          ▼            ▼
   Analyst     Developer     QA         Reviewer    Scorer
   Agent       Agent         Agent      Agent       (SQLite)
        │          │           │
        ▼          ▼           ▼
   TaskSpec    file tree   Docker container
   (JSON)      + git ops   ruff/mypy/bandit/pytest
                               │
                    ┌──────────┴──────────┐
                    │   Model Router      │
                    │   (random, by role) │
                    └──────────┬──────────┘
                               │
                    ┌──────────┴──────────┐
                    │      Ollama         │
                    │  qwen3-coder:30b    │
                    │  devstral:24b       │
                    │  glm-4.7-flash  …   │
                    └─────────────────────┘
```

### Pipeline state

All agents share a `PipelineContext` dataclass — a single object passed through the
entire pipeline. It holds the issue, the task spec, QA reports, review results, commits,
and execution logs. Nothing is global; everything is traceable.

### Model rotation

Each agent role (analyst, developer, reviewer) selects a model **randomly** from the
registered models that declare that role. This enables A/B comparisons across many
pipeline runs without any manual configuration.

Two constraints narrow the random draw:

- **Reviewer diversity** — the reviewer sets `avoid_repeated_model = True`, so the second
  review pass skips the model used by the first and offers a genuinely different
  perspective. Other roles reuse their model freely (the developer *must*, so that a QA
  retry keeps the model that already has the context).
- **Agentic-loop capability** — with the `opencode` developer backend the model has to
  actually drive a tool-calling loop. Ollama's `tools` capability flag is necessary but
  **not** sufficient: several tool-capable models simply answer in prose and edit nothing.
  Only models verified to drive the loop carry `drives_agentic_loop=True` in the registry,
  and the developer requires that flag when the backend is `opencode`.

### Developer backends

| Backend | How it works | When to use |
|---|---|---|
| `ollama` *(default)* | Single-shot LLM call; the model returns whole files, DevFactory writes them | Simple, single-file changes |
| `opencode` | Drives the [OpenCode](https://opencode.ai) CLI in `--auto` mode against a local Ollama model; the model reads, greps and edits files itself | Multi-file, context-dependent changes |

The `opencode` backend needs a large context window on the Ollama side —
set `OLLAMA_CONTEXT_LENGTH=32768` (or more) in the Ollama service environment.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | |
| Docker | any recent | For the QA isolation container |
| Ollama | latest | Running locally, at least one model pulled |
| GitHub | — | Personal access token with `repo` + `pull_request` scopes |
| GPU | recommended | RTX 3090 24 GB VRAM or equivalent — the registry targets 20–30B models |
| OpenCode | optional | Only for the `opencode` developer backend |

---

## Installation

> Uses [`uv`](https://github.com/astral-sh/uv) for environment and dependency
> management (some systems ship Python without the `venv` module). With `uv`
> installed, the steps below work out of the box.

```bash
# Clone the repository
git clone https://github.com/your-org/devfactory.git
cd devfactory

# Create a virtual environment with uv (recommended)
uv venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# Install in editable mode
uv pip install -e .

# For development (includes ruff, mypy, pytest)
uv pip install -e ".[dev]"
```

---

## Configuration

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | *(required)* | Personal access token — needs `repo` and `pull_request` scopes |
| `GITHUB_USERNAME` | *(required)* | Your GitHub username |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_TIMEOUT_S` | `300` | Seconds before an LLM call times out |
| `DEVFACTORY_POLL_INTERVAL` | `60` | Seconds between GitHub polls |
| `DEVFACTORY_DB_PATH` | `./devfactory.db` | SQLite knowledge-base path |
| `DEVFACTORY_WORKSPACE` | `/tmp/devfactory` | Directory where repos are cloned |
| `DEVFACTORY_MAX_QA_RETRIES` | `3` | Max Developer → QA loop iterations |
| `DEVFACTORY_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` |
| `DEVFACTORY_DEV_BACKEND` | `ollama` | Developer backend: `ollama` (single-shot) or `opencode` (agentic CLI) |
| `OPENCODE_BIN` | `~/.opencode/bin/opencode` | OpenCode CLI path — used only by the `opencode` backend |
| `OPENCODE_TIMEOUT_S` | `1800` | Seconds before an OpenCode run is killed |
| `DOCKER_TEST_IMAGE` | `devfactory-test:latest` | Name of the pre-built QA image |

---

## First-time setup

Run the `init` command once per repository. It:

1. Creates the DevFactory labels in your GitHub repo.
2. Builds the Docker QA image.
3. Checks that Ollama is reachable.
4. Initialises the SQLite database.

```bash
devfactory init --repo owner/repo
```

---

## Usage

### Process a single issue

```bash
devfactory run --issue 42 --repo owner/repo
devfactory run --issue 42 --repo owner/repo --verbose   # debug output
```

### Continuous polling

```bash
devfactory poll --repo owner/repo
```

The poller picks up issues labelled `ready-for-dev`, processes them one at a time, and
loops every `DEVFACTORY_POLL_INTERVAL` seconds. Press `Ctrl+C` to stop.

### View model performance stats

```bash
devfactory stats                            # full dashboard
devfactory stats --role developer           # filter by agent role
devfactory stats --metric tests_pass_rate   # filter by metric
```

### List and sync models

```bash
devfactory models             # show registry vs. Ollama availability
devfactory models --sync      # register all Ollama models in the KB
```

### Browse run logs

```bash
devfactory logs --issue 42         # last run for this issue
devfactory logs --issue 42 --all   # all runs for this issue
```

### GitHub workflow

DevFactory manages these labels automatically:

| Label | Meaning |
|---|---|
| `ready-for-dev` | **You set this** — triggers the pipeline |
| `devfactory:in-progress` | Pipeline is running |
| `devfactory:ready-for-review` | PR is open, ready for your review |
| `devfactory:qa-failed` | QA exhausted all retries |
| `devfactory:error` | Pipeline crashed (check logs) |

---

## Adding models

Edit `devfactory/models/registry.py` and add a `ModelMeta` entry:

```python
ModelMeta(
    name="devstral:24b",
    parameters_b=24,
    context_k=32,
    roles=["developer", "reviewer"],
    # Set to True only after verifying the model actually emits tool calls in
    # OpenCode — Ollama's "tools" capability flag is not enough.
    drives_agentic_loop=False,
    notes="Mistral's agentic coder",
),
```

Then pull the model in Ollama:

```bash
ollama pull devstral:24b
```

The model will be selected randomly for its declared roles on the next pipeline run.
Models below ~20B are deliberately not registered: they cost more retries than they save.

**Roles:**

| Role | Agent | Description |
|---|---|---|
| `analyst` | `AnalystAgent` | Parses issue → structured `TaskSpec` |
| `developer` | `DeveloperAgent` | Generates/modifies code |
| `qa` | `QAAgent` | Interprets Docker QA results (no LLM call currently) |
| `reviewer` | `ReviewerAgent` | Code review → inline GitHub PR comments |

---

## Knowledge base & scoring

Every pipeline run is recorded in `devfactory.db` (SQLite).

### Schema

```sql
models      (id, name, parameters_b, provider, notes, added_at)
tasks       (id, github_issue_id, repo, status, branch_name, pr_url, …)
executions  (id, task_id, model_id, agent_type, prompt_tokens,
             completion_tokens, duration_ms, created_at)
scores      (id, execution_id, metric, value, notes, created_at)
```

### Metrics recorded

| Metric | Source | Range |
|---|---|---|
| `tests_pass_rate` | pytest passed / total | 0.0 – 1.0 |
| `lint_score` | ruff issue count | 0.0 – 1.0 |
| `security_score` | bandit severity | 0.2 / 0.5 / 0.8 / 1.0 |
| `review_verdict` | reviewer verdict | 0.3 / 0.6 / 1.0 |
| `review_quality` | reviewer self-score | 0.0 – 1.0 |
| `retry_count` | QA iterations | 0, 1, 2, … |

After enough pipeline runs you get an objective, data-driven ranking of which local
models perform best for which roles — without any subjective opinion.

---

## Project structure

```
devfactory/
├── devfactory/
│   ├── agents/
│   │   ├── base.py          # BaseAgent: model selection, prompt loading, LLM call
│   │   ├── analyst.py       # Issue → TaskSpec (JSON)
│   │   ├── developer.py     # TaskSpec → code files
│   │   ├── qa.py            # Orchestrates the Docker QA runner
│   │   └── reviewer.py      # Diff + QA → inline GitHub review
│   ├── qa/
│   │   └── runner.py        # Docker QA execution (ruff/mypy/bandit/pytest)
│   ├── github/
│   │   ├── client.py        # Lazy PyGitHub singleton
│   │   ├── git_ops.py       # Clone, branch, commit, push (GitPython)
│   │   ├── issues.py        # Issue fetching and label management
│   │   ├── poller.py        # Polling loop
│   │   ├── pr.py            # PR creation
│   │   └── review.py        # Inline review posting (diff-position mapping)
│   ├── kb/
│   │   ├── database.py      # SQLite schema and queries
│   │   ├── scorer.py        # Scoring logic (called after pipeline)
│   │   └── dashboard.py     # Rich terminal dashboard
│   ├── models/
│   │   ├── client.py        # Ollama API wrapper
│   │   ├── registry.py      # Model catalogue — edit this to add models
│   │   ├── router.py        # Random model selection by role
│   │   └── retry.py         # Retry decorator for network calls
│   ├── config.py            # Pydantic settings
│   ├── context.py           # PipelineContext dataclass
│   ├── orchestrator.py      # Sequential pipeline logic
│   ├── repo_context.py      # Workspace file reader for developer context
│   ├── logging_setup.py     # Rich + JSON-lines logging
│   └── cli.py               # Typer CLI
├── prompts/
│   ├── analyst.md           # Analyst system prompt
│   ├── developer.md         # Developer system prompt
│   └── reviewer.md          # Reviewer system prompt
├── docker/
│   └── Dockerfile.test      # QA test environment
├── docs/
│   └── VISION.md            # Product direction + compliance architecture & roadmap
├── tests/                   # Unit tests (no Ollama or GitHub required)
├── .env.example             # Environment variable template
├── CLAUDE.md                # Claude Code instructions for this repo
├── CONTRIBUTING.md          # Contribution guide
├── CHANGELOG.md             # Version history
└── pyproject.toml           # Package metadata and tool configuration
```

---

## Roadmap

### Compliance track

The phased plan (P0 → P3) and its exit evidence live in [docs/VISION.md](docs/VISION.md).

- [ ] **P0 — infosec foundation** — audit-grade immutable run logs, documented change
      management, access/secrets review, written policies *(SOC 2 · ISO 27001)*
- [ ] **P1 — traceability spine** — entity model + hash-chained record store + audit
      package export (issue → requirement → code → test → verification → review → release)
- [ ] **P2 — gates, roles & sign-off** — enforced phase gates, recorded human approvals,
      documented separation of duties
- [ ] **P3 — medical uplift** — safety classification, ISO 14971 risk hook, problem
      resolution, tool-validation dossier, Design History File export *(IEC 62304 · ISO 13485)*

### Capability track

- [ ] **vLLM backend** — drop-in replacement for Ollama with better concurrency
- [ ] **Optional cloud model** — opt-in fallback to a frontier API for hard tasks
      (off by default; DevFactory stays fully local unless you enable it)
- [ ] **Empty-change guard** — fail the run when the developer produced no diff, instead
      of pushing an empty branch and hitting a PR 422
- [ ] **Parallel pipeline** — run multiple issues concurrently
- [ ] **Web dashboard** — visualise KB stats and pipeline runs in the browser
- [ ] **Integration tests** — end-to-end tests against a test GitHub repository
- [ ] **Multi-language projects** — extend QA runner for Node.js, Go, Rust
- [ ] **Architect agent** — decompose large issues into sub-tasks automatically
- [ ] **Auto-merge** — optional automatic merge when all reviewers approve
      *(kept off for regulated work: the human gate is the control)*

---

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions,
coding standards, and how to submit changes.

---

## License

MIT — see [LICENSE](LICENSE).
