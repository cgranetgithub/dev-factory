# Changelog

All notable changes to DevFactory are documented here.
This project follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

**Vocabulary — breaking**
- The pipeline step formerly called *QA* is renamed **verification**: it runs static
  analysis and executes tests, which is "did we build it right" (IEC 62304 §5.5.5 /
  §5.6 / §5.7, SOC 2 CC8.1). *Validation* — acceptance criteria / UAT — is deliberately
  left free for a future gate.
- Renamed: `devfactory.qa` package → `devfactory.verification`, `QAAgent` →
  `VerificationAgent` (role `qa` → `verification`), `QARunner` → `VerificationRunner`,
  `QAReport` → `VerificationReport`, `QAFailedError` → `VerificationFailedError`,
  `ctx.qa_report` / `ctx.qa_attempts` → `ctx.verification_report` /
  `ctx.verification_attempts`, label `devfactory:qa-failed` →
  `devfactory:verification-failed`, setting `DEVFACTORY_MAX_QA_RETRIES` →
  `DEVFACTORY_MAX_VERIFICATION_RETRIES`, task status `qa_failed` →
  `verification_failed`.
- Historic KB rows are migrated on startup, so the audit trail keeps one vocabulary
  across its whole series rather than changing meaning mid-stream.

**Direction**
- `docs/VISION.md` — product direction and compliance architecture: local-first + auditable,
  SOC 2 / ISO 27001 first, IEC 62304 / ISO 13485 next, with a phased P0 → P3 roadmap
- README: "Vision & compliance" section, compliance track in the roadmap
- `docs/VISION.md`: control-monitoring section — what can drift, the recorded baseline of
  the repository's enforced configuration (with three identified gaps), the planned
  `devfactory controls check`, and the attribution limit of a non-Enterprise repository

**Verification**
- `.github/workflows/ci.yml` — ruff, mypy, bandit and pytest re-run on GitHub, so the verification step
  claim is enforced by the platform instead of asserted by the audited pipeline

**Developer backend**
- Pluggable developer backend (`DEVFACTORY_DEV_BACKEND`): `ollama` (single-shot) or
  `opencode` (agentic CLI loop over a local Ollama model)
- `ModelMeta.drives_agentic_loop` — Ollama's `tools` capability is not sufficient; only
  models verified to actually emit tool calls in OpenCode are eligible for that backend
- `ModelRouter.select(..., require_agentic_loop=)` and `BaseAgent.avoid_repeated_model`
  (reviewer-only), so Verification retries no longer starve the developer's model pool

**Models**
- Raised the registry floor to 20B; coding pool is now qwen3-coder:30b, devstral:24b,
  qwen2.5:32b (codestral:22b removed — no tool support)

---

## [0.1.0] — 2026-06-22

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
  `devfactory:verification-failed`, `devfactory:error`

**Knowledge base**
- SQLite schema: `models`, `tasks`, `executions`, `scores`
- Automatic scoring after each pipeline run (tests pass rate, lint score, security, review verdict)
- Rich terminal dashboard: leaderboard, per-role metric breakdown, task status summary
- `devfactory stats` and `devfactory logs` CLI commands

**Developer experience**
- `devfactory init` — one-command setup (labels, Docker image, Ollama check, DB)
- `devfactory models --sync` — sync Ollama models into the KB
- 27 unit tests, zero external dependencies required for test suite
