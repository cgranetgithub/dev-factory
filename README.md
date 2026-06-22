# DevFactory

> A local AI-powered software factory that turns GitHub Issues into Pull Requests —
> entirely on your own hardware.

DevFactory runs a sequential SDLC agent pipeline using small local LLMs (via Ollama).
It watches your GitHub repository for issues labelled `ready-for-dev`, processes them
one by one through a structured workflow, and opens a reviewed Pull Request — ready for
your final approval.

No cloud LLM costs. No data leaves your machine. Every model run is scored and stored
so you can compare models objectively over time.

---

## Table of contents

- [How it works](#how-it-works)
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
                    │  qwen2.5-coder:7b   │
                    │  deepseek-coder:16b │
                    │  mistral:7b  …      │
                    └─────────────────────┘
```

### Pipeline state

All agents share a `PipelineContext` dataclass — a single object passed through the
entire pipeline. It holds the issue, the task spec, QA reports, review results, commits,
and execution logs. Nothing is global; everything is traceable.

### Model rotation

Each agent role (analyst, developer, qa, reviewer) selects a model **randomly** from the
registered models that support that role. The previous model used in a pipeline run is
excluded from the next selection, so the two reviewers always use different models.

This enables A/B comparisons across many pipeline runs without any manual configuration.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | |
| Docker | any recent | For the QA isolation container |
| Ollama | latest | Running locally, at least one model pulled |
| GitHub | — | Personal access token with `repo` + `pull_request` scopes |
| GPU | recommended | RTX 3090 24 GB VRAM or equivalent for 7–16B models |

---

## Installation

```bash
# Clone the repository
git clone https://github.com/your-org/devfactory.git
cd devfactory

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# Install in editable mode
pip install -e .

# For development (includes ruff, mypy, pytest)
pip install -e ".[dev]"
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
    name="codellama:13b",
    parameters_b=13,
    context_k=16,
    roles=["developer", "reviewer"],
    notes="Good at code completion tasks",
),
```

Then pull the model in Ollama:

```bash
ollama pull codellama:13b
```

The model will be selected randomly for its declared roles on the next pipeline run.

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
│   │   ├── qa.py            # Orchestrates Docker QA runner
│   │   └── reviewer.py      # Diff + QA → inline GitHub review
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
├── tests/                   # Unit tests (no Ollama or GitHub required)
├── .env.example             # Environment variable template
├── CLAUDE.md                # Claude Code instructions for this repo
├── CONTRIBUTING.md          # Contribution guide
├── CHANGELOG.md             # Version history
└── pyproject.toml           # Package metadata and tool configuration
```

---

## Roadmap

- [ ] **vLLM backend** — drop-in replacement for Ollama with better concurrency
- [ ] **Parallel pipeline** — run multiple issues concurrently
- [ ] **Web dashboard** — visualise KB stats and pipeline runs in the browser
- [ ] **Integration tests** — end-to-end tests against a test GitHub repository
- [ ] **Multi-language projects** — extend QA runner for Node.js, Go, Rust
- [ ] **Architect agent** — decompose large issues into sub-tasks automatically
- [ ] **Auto-merge** — optional automatic merge when all reviewers approve

---

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions,
coding standards, and how to submit changes.

---

## License

MIT — see [LICENSE](LICENSE).
