# Contributing to DevFactory

Thank you for your interest in contributing! This document explains the development
workflow, coding standards, and how to get your changes merged.

## Table of contents

- [Development setup](#development-setup)
- [Project structure](#project-structure)
- [Coding standards](#coding-standards)
- [Testing](#testing)
- [Submitting changes](#submitting-changes)
- [Areas where help is welcome](#areas-where-help-is-welcome)

---

## Development setup

```bash
# Clone the repository
git clone https://github.com/your-org/devfactory.git
cd devfactory

# Create a virtual environment with uv (recommended)
uv venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install in editable mode with dev tools
uv pip install -e ".[dev]"

# Copy and fill in environment variables
cp .env.example .env
# → edit .env with your GITHUB_TOKEN and GITHUB_USERNAME

# Build the Docker test image (required for QA tests)
docker build -f docker/Dockerfile.test -t devfactory-test:latest .

# Pull at least one Ollama model
ollama pull qwen2.5-coder:7b

# Run the test suite
python -m pytest tests/ -v
```

---

## Project structure

```
devfactory/
├── devfactory/
│   ├── agents/          # Agent implementations (analyst, developer, qa, reviewer)
│   ├── github/          # GitHub integration (poller, git operations, PR, reviews)
│   ├── kb/              # Knowledge base: SQLite schema, scorer, dashboard
│   ├── models/          # LLM client, model registry, router, retry logic
│   ├── config.py        # Pydantic settings (reads from .env)
│   ├── context.py       # PipelineContext — shared state passed between agents
│   ├── orchestrator.py  # Sequential pipeline logic
│   ├── repo_context.py  # Reads workspace files to inject into developer prompts
│   ├── logging_setup.py # Rich + JSON-lines logging
│   └── cli.py           # Typer CLI commands
├── prompts/             # Markdown prompt templates (loaded at runtime)
├── docker/              # Dockerfile for the isolated QA test environment
└── tests/               # Pytest unit tests (no Ollama or GitHub required)
```

---

## Coding standards

### Language

- **Python 3.11+** with type hints on all public functions.
- Always include `from __future__ import annotations` at the top of every module.

### Style

- Max line length: **100 characters** (enforced by ruff).
- Imports sorted by: stdlib → third-party → devfactory (ruff isort).
- Run `ruff check .` and `ruff format .` before committing.

### Docstrings

All public modules, classes, and functions must have docstrings.
Use the Google docstring style for functions with arguments:

```python
def do_thing(x: int, y: str) -> bool:
    """
    One-line summary.

    Longer description if needed.

    Args:
        x: Description of x.
        y: Description of y.

    Returns:
        True if the thing succeeded.

    Raises:
        ValueError: If x is negative.
    """
```

### Exception handling

- Catch specific exceptions, not bare `except:` or `except Exception:`.
- Always log the exception before swallowing it (use `logger.warning` or `logger.error`).
- Let unexpected exceptions propagate to the orchestrator, which handles status updates.

### Logging

```python
import logging
logger = logging.getLogger(__name__)

# Use this — never print() in library code
logger.info("[agent] doing something")
```

### Prompts

Prompts live in `prompts/<role>.md` and are loaded at runtime by `BaseAgent.load_prompt()`.
They are versioned with the code. Keep them focused and testable (the LLM output format
must be parseable by the agent's `_parse_*` methods).

---

## Testing

```bash
# Run all unit tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_scorer.py -v

# Run with coverage
python -m pytest tests/ --cov=devfactory --cov-report=term-missing
```

### Writing tests

- Unit tests must not require a running Ollama instance or GitHub access.
- Use `Scorer(database=make_db())` pattern for DB isolation.
- Place integration tests (that call real APIs) in `tests/integration/` with
  a `@pytest.mark.integration` mark, and document the required setup.

---

## Submitting changes

1. **Fork** the repository and create a branch from `main`:
   ```bash
   git checkout -b feat/my-feature
   ```

2. **Make your changes** following the coding standards above.

3. **Run the checks**:
   ```bash
   ruff check .
   ruff format --check .
   python -m pytest tests/ -v
   ```

4. **Commit** with a clear, conventional commit message:
   ```
   feat: add support for vLLM as an alternative backend
   fix: handle empty diff in reviewer prompt
   docs: add example for custom model registration
   ```

5. **Open a Pull Request** against `main`. Include:
   - A description of what changed and why.
   - Any relevant issue numbers (`Closes #N`).
   - Notes on testing (what you ran, what you verified).

---

## Areas where help is welcome

- **New model integrations**: vLLM backend, OpenAI-compatible APIs, llama.cpp.
- **New agent roles**: Security auditor, Documentation writer, Dependency updater.
- **Parallel pipeline**: Concurrent agent execution for faster throughput.
- **Web UI**: A simple dashboard to visualise the KB stats and pipeline runs.
- **Integration tests**: Tests that verify the full pipeline against a test repo.
- **Model evaluation metrics**: More sophisticated scoring (semantic similarity, AST analysis).
- **Prompt improvements**: Better prompts → better agent output → better benchmarks.

If you have a question, open an issue with the `question` label before starting a large change.
