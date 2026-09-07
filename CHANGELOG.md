# Changelog

All notable changes to DevFactory are documented here.
This project follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

**Scope gate**
- A third gate, running before the container and before the reviewer: does the change
  touch the files the task declared? Two pull requests had already reached a human with
  a feature nothing calls — the new code written, the one line wiring it in skipped —
  and every existing gate passed them
- Missing declared files send the change back with the filenames as feedback. Files
  changed *outside* the task are reported but never block: an analyst cannot foresee
  every file a correct change needs
- `scope_rejections` joins the shared iteration budget, so a bad file list cannot loop
  forever. The analyst now logs the files it declared, since they are a gate input

**Models**
- `qwen3.8:27b` replaces `qwen3.6:27b`. Same size on disk and the same 4/4 over two
  trials, but 42s and 58s against 670s and 153s — which removes the only reason its
  predecessor was kept out of the `developer` role. The agentic developer pool is now
  four models. Re-measured rather than inheriting the flag: a version bump is a
  different model

**Self-provisioning**
- The pipeline prepares the host before spending anything on a run: it reports the Ollama
  version against a validated minimum (`DEVFACTORY_MIN_OLLAMA_VERSION`, warn — never
  block), and pulls any registry model Ollama is missing
  (`DEVFACTORY_AUTO_PULL_MODELS`, on by default). Previously the router silently skipped
  missing models, so a registry of six could quietly behave like a registry of one
- `devfactory init` provisions models too, which is the right moment for a multi-GB
  download

**OpenCode provider config**
- The developer generates OpenCode's provider config from the registry and passes it via
  `OPENCODE_CONFIG_CONTENT`, instead of relying on a hand-maintained file outside the
  project. A model the registry declared but that file omitted failed at run time with
  `ProviderModelNotFoundError`, after the analyst had already run

**Developer pool**
- `gemma4:26b` and `glm-4.7-flash` take the `developer` role alongside `qwen3-coder:30b`:
  they qualified as agentic drivers on both trials and are fast enough for a loop that
  may run three times behind two gates. The role now has three drivers instead of one
- `qwen3.6:27b` stays out of it on speed alone (670s then 153s), not capability
- The coding/general split no longer decides who can develop — capability does

**Model qualification — a correction**
- `drives_agentic_loop` was wrong for four models. Re-measured on a realistic task
  (edit two files, run pytest and ruff, fix what they report), scored from outside the
  model on four criteria, over two identical trials: `gemma4:26b`, `glm-4.7-flash` and
  `qwen3.6:27b` all drive the loop, alongside `qwen3-coder:30b`
- The original verdict was taken while Ollama still used its 4096-token default context,
  where the tool definitions overflow and the model answers in prose — indistinguishable
  from incapacity. The registry comment now records this so the next reader re-measures
  rather than trusting the flag
- `qwen2.5:32b` stays excluded, for instability rather than incapacity: 4/4, then a
  timeout, then 0/4 on the same exercise. `devstral:24b` stays excluded — it fails even
  the trivial task after the context fix
- New `--model role=name` on `devfactory run`, repeatable, so a comparison run pins its
  models instead of drawing them at random. Unknown roles, unknown models and
  role/model mismatches are rejected rather than silently ignored

**Verification environment**
- `docker/Dockerfile.test` installs `git` and configures an identity. GitPython raises
  on import without the binary, so any test module importing it failed to *collect* —
  reported as `0 passed, 0 failed` plus a collection error — and the gate then failed
  every run of this repository regardless of what the developer produced
- New `verification-image` CI job builds that image and runs the suite inside it. The
  GitHub runner has git and the slim image does not, which is how the divergence stayed
  invisible while CI was green

**Review becomes a gate**
- The reviewer moves inside the developer loop and its verdict is now acted on:
  `changes_requested` sends the change back to the developer with the review comments,
  anything else lets it proceed. Previously two reviews ran after the PR was opened and
  nothing consumed the result
- Order is verification first, then review: the deterministic gate costs a couple of
  minutes and the review costs a model call, so the reviewer never spends its judgement
  on code that does not even pass its own tests
- Both gates draw on one shared budget of developer iterations (`ctx.iterations_used`),
  so a change cannot ping-pong between them
- The review that governed the accepted iteration is posted onto the PR — one review,
  two publications: it decides inside the loop, and it is the record at the point where
  the human approves
- If the budget runs out with the reviewer still unsatisfied, the PR is opened anyway
  with a warning banner and `ctx.review_unresolved` set: a gate that was not satisfied
  stays visible in the evidence rather than blocking the run silently
- The reviewer prompt now receives the acceptance criteria and is told to judge intent
  and design, not formatting — verification already covers that

**Developer accountability**
- `prompts/developer_opencode.md` gains an explicit definition of done: the agent runs
  `ruff check --fix`, `ruff format`, `ruff check` and `pytest -q` itself and does not
  finish while any of them fails, with named guidance for the three failures observed in
  practice (unsplittable `E501`, uncollectable test files, undefined names)
- Autofix now measures the lint issues the developer left behind *before* fixing them,
  and the scorer records it as `lint_left_behind`. Without it the pipeline cleaned up
  after the model and then scored the cleaned result; `lint_score` is now annotated as
  post-autofix

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
