# Changelog

All notable changes to DevFactory are documented here.
This project follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

**The watchdog was the hang**

- The OpenCode startup watchdog added in #78 never detected a hang. It called
  `communicate(timeout=120)` and then probed the pipes with `select` — but waiting *is*
  reading, so the timed-out call had already drained both pipes and the probe found them
  empty every time. The 120 seconds was a cap on total runtime, and OpenCode was being
  killed for being slow. The three "intermittent harness hangs" behind #78 and #88 were
  this, deterministically; OpenCode writes its bootstrap log half a second after launch
  and was never the culprit. The verdict now comes from `TimeoutExpired.stdout/.stderr`,
  where the output actually is
- A genuine startup hang now costs one retry instead of the whole run: the process is
  killed and launched once more. Two consecutive hangs raise, and a timeout *after* output
  has started is not retried — that is real work running long
- `devfactory run` exits non-zero when the pipeline raised. It returned 0 on a failed run,
  which cannot be scripted

**A run starts from what the repository contains**

- `git_ops.setup_branch` claimed to reset the workspace and only checked out. A run that
  died mid-developer left its edits in the tree, and every later run on any issue then
  died at `git checkout main`. The quieter half is worse: when the checkout did succeed,
  the leftovers became what the analyst read — one spec issue asserts that a function
  "already contains a partial implementation" of code that exists in no commit and no
  repository. Evidence contaminated in silence

**The gate is as careful about itself as about the code**

- mypy runs over the project **with its dependencies installed**. It ran against the bare
  image, so every third-party import was `Any`: it invented errors that exist only without
  dependencies — that is how `main` failed its own gate while CI was green — and missed
  every error that needs them. mypy and pytest now share one prepared copy and one
  install; measured cost **+2.5s per verification run**, and mypy genuinely does more work
  once the types resolve
- CI's `verification-image` job type-checks the same way, so the gate and CI cannot
  disagree again without a red build
- `pip install .` builds in-tree, leaving `build/lib/devfactory`, which mypy reads as a
  duplicate module and reports as "did not run". The install removes it — but only when
  the install created it

**Unified diffs are parsed, not scanned**

- Both hand-written diff parsers were wrong on input `git diff` really produces.
  `_build_diff_position_map` counted `\ No newline at end of file` as a context line, so it
  invented a file line and shifted every position after the marker; `truncate_diff` split
  on the string `diff --git `, so on a commit that *adds* a `.diff` file it found five
  sections and reported three omitted files the commit never touched
- `unidiff` replaces both. It is used as a locator, not a serialiser: file boundaries and
  paths come from it, the diff text is still sliced from the bytes git wrote. Two things it
  does not do are handled explicitly — GitHub serves a patch body with no `diff --git`
  header (one is synthesised before parsing), and it discards the `--stat` preamble that
  #60 exists to preserve (sliced from the raw text instead)
- The existing tests' fixtures were corrected: they declared hunk headers whose counts did
  not match their bodies. Legal to a string scan, never emitted by git, rejected by a parser

**Controls are checked, not declared**

- `devfactory controls check --repo owner/repo` snapshots what GitHub actually enforces on
  the default branch — rulesets and their parameters, bypass actors, effective branch
  rules, collaborators and roles, a hash of CODEOWNERS — records it append-only in the
  knowledge base with a timestamp, and reports drift against the previous snapshot field by
  field. Exit 1 on drift, 2 when the API cannot be read
- The series of records *is* the evidence: SOC 2 and ISO 27001 ask whether a control
  operated throughout the period, which a point-in-time screenshot cannot answer. So the
  table takes inserts and reads, and has no update or delete
- Its first real run on this repository found **no `required_status_checks` rule in the
  ruleset** — CI runs on every pull request but nothing requires it to pass. That is one of
  the gaps #20 lists, found by the tool rather than by reading settings
- The documented limit: GitHub's audit-log API is Enterprise-only, so on a personal
  repository we can show *that* a control changed and *when*, never *by whom*

**Community**

- `SECURITY.md` describes the real threat surface — a repo-scoped token, model-generated
  code running in a container, third-party issue text reaching a model — and routes private
  reports through GitHub. `CODE_OF_CONDUCT.md`, issue templates and a pull-request template
  complete the community profile. The factory-task template is shaped like the issues that
  actually worked as pipeline input, because for this repository the template is part of
  the prompt

**The specification is the issue**
- The developer, the reviewer, the scope gate and the pull request body all fetch the
  specification from its GitHub issue, each time they need it. It was published there and
  then handed on as a Python copy, which made the issue decoration: amending it changed
  nothing, because the copy was what got built
- `PipelineContext.task_spec` is gone, and with it the last hand-off between agents. The
  autonomy rule now holds in code, not only in the architecture document
- The spec issue body is parsed back tolerantly — ticked checkboxes, `*` bullets, prose over
  several lines, an unknown section ignored rather than misfiled — because a human is
  expected to edit it. It asks the reader to keep the section headings, since that is how
  it is read
- A resumed run no longer re-runs the analyst: it finds the spec issue it already published
  and reads that. Re-running spent a model call to overwrite whatever a human had amended
  while the run was down

**A tool that did not run is not a pass**
- Every verification tool result carries a status — `clean`, `findings` or `error` — and an
  error fails the report whatever the other tools say. Empty output was read as "no
  findings": ruff unable to write its cache to the read-only mount printed an error, exited
  2, and the gate passed code it had never checked. Mypy the same, and so did a missing
  binary, a timed-out container and a project that would not install
- Classification reads the exit code first and the output second. Ruff exiting 1 with an
  empty finding list is an error too: a tool that contradicts itself has delivered no verdict
- The summary names each tool that did not run and shows the tail of its output. The
  developer cannot fix a finding nobody made, but it can often fix what stopped the tool
- A container timeout is a verification result, not a pipeline crash

**Three regressions left by the migrations**
- The developer retried blind. The graph state held the counters and the context mirrored
  them only after the loop, so on every retry the prompt saw them at zero and showed no
  feedback from the gate that had just refused
- The developer could draw a prose-only model. The override that prevented it was removed
  with the single-shot backend; the router now filters on `drives_agentic_loop` for every
  role, always
- `last_gate_passed` lived on the `Pipeline` object rather than in the graph state, so a
  resumed run — which gets a fresh object — read "not passed" and went back to the developer
  regardless of what had actually happened

**Housekeeping**
- `ruff check --fix` and `ruff format` run as pre-commit hooks, from the environment rather
  than a pinned mirror, so the hook and CI cannot disagree about what is clean. With
  `dismiss_stale_reviews_on_push` enabled, a formatting fix pushed after approval costs the
  approval — paid several times
- Dependabot watches `pip` and `github-actions` weekly, grouped; `.editorconfig` states to
  the editor what ruff enforces
- The published URLs point at the real repository. The scaffolding placeholder had survived
  into the package metadata, the README, `CONTRIBUTING.md` and the footer of every pull
  request the factory opened — a 404 at the first thing a visitor clicks
- Dead code removed after the migrations: the unused analyst token ceiling, the superseded
  developer prompt, `_GENERAL_ROLES`, the reviewer's in-loop GitHub posting, the message
  helpers, and `review_unresolved` from the graph state. `mark_qa_failed` becomes
  `mark_verification_failed`, the last survivor of the rename
- `retry_count` in the knowledge base records the iterations actually used, split by gate

**Harness and gates**
- The OpenCode runner gains a **startup deadline**: a run that has written nothing after
  120s has hung before reaching the model — observed twice — and is abandoned instead of
  waiting out the 1800s limit. The hang is OpenCode's; this stops it costing half an hour
- `END_NODE` is annotated `str`. mypy sees a different world in CI (project installed)
  than in the verification container (bare image), and an untyped `END` made `main` fail
  its own gate while CI stayed green
- `checkpoints.sqlite` is removed from version control and ignored

**CI**
- The `qa` job runs a matrix of Python 3.11, 3.12 and 3.13 — every version the project
  claims. `verification-image` stays single-version: it exists to test the image the
  factory actually uses, which is pinned to 3.11

**Documentation catch-up**
- `CLAUDE.md` and `CONTRIBUTING.md` still described the pre-rename package (`qa/`, a `qa`
  role) and a pipeline with two backends and no gates. Both now match the code
- The pipeline-flow block shows the three gates, the shared budget, the spec issue and
  the graph, so a debugging reader sees the real sequence
- "Adding a new agent" says the two things a new agent must respect: it reaches its model
  through the harness, and it must be able to work from published artifacts

**One path, not two**
- The single-shot developer backend is removed, with `repo_context.py`, the
  `DEVFACTORY_DEV_BACKEND` setting, `BaseAgent.chat()`, `LLMResponse` and the retry
  decorator — 775 lines. It existed because the agentic loop appeared not to work, which
  was a measurement error (#33), and a developer-only fallback cannot help when the
  analyst and the reviewer need the harness too
- `OllamaClient` keeps the management surface the pipeline uses — `version`,
  `list_models`, `pull_model`. Inference goes through the harness now
- OpenCode moves from optional to required in the prerequisites, which is what it has
  actually been since the analyst started reading the codebase

**The flow is a graph**
- The developer → gates loop is a LangGraph `StateGraph` with conditional edges. The
  `while True` and its four near-identical rejection blocks are gone; every routing
  decision is in one function
- Runs are checkpointed to `checkpoints.sqlite`. A run that dies after a sixteen-minute
  developer step resumes with `devfactory run --resume <thread-id>`, printed at the start
  of every run. A fresh thread per run, not per issue — reusing the issue number would make
  a deliberate re-run silently resume a half-finished one
- The graph state holds orchestration only: the counters, and nothing an agent produced.
  A resumed node re-reads the world rather than needing a serialised copy of it

**The reviewer reads the code**
- It runs through OpenCode read-only in the repository, on the branch, instead of judging
  a diff alone. It had approved three real defects, each invisible in the diff and obvious
  one file away
- It refuses to share the developer's model — an agent reviewing its own work is not a
  review. `BaseAgent.avoid_models_from_roles` makes that a declared control rather than a
  coincidence of the random draw
- A reviewer that modified the working tree fails the run. OpenCode's `plan` agent forbids
  it; this checks rather than trusts, because the failure would otherwise be silent

**The analyst reads the code**
- It runs through OpenCode read-only (`--agent plan`) with the checkout, instead of seeing
  only the issue text. It had been declaring filenames it had never opened
- Its specification is published as a **linked GitHub issue** labelled `devfactory:spec`,
  cross-referenced both ways, updated rather than duplicated on a re-run. A human can read
  and amend it before development starts
- The clone now happens **before** the analyst — it needs a checkout to read
- One shared OpenCode runner (`devfactory/opencode.py`) for every agent that needs the
  codebase, instead of a second copy of the invocation

**Empty changes**
- An iteration that produces nothing is sent back to the developer instead of being
  committed and gated. Closes the hole behind the original `422 No commits between`:
  the container verifies a tree that is still green and passes it, and the reviewer
  reads an empty diff
- Folded into the scope gate rather than added as a fourth one — "the change does not
  cover what was asked" is one question, and producing nothing is its extreme case

**Run autonomy**
- The pipeline applies the issue's status labels itself, whatever the outcome. They lived
  in the poller, so a run started from the CLI left the issue labelled `ready-for-dev` and
  the poller would pick it up again. A GitHub failure there is logged, never fatal
- Factory pull requests arm auto-merge (squash) on creation, so GitHub merges once the
  approval requirement is met. Approved factory PRs were sitting unmerged because nobody
  had armed them — the opposite of autonomous. The human approval stays the gate

**Analyst**
- An unparseable analyst response no longer degrades into an empty `TaskSpec` that the
  pipeline runs on anyway. The analyst is asked again, with the problem named, up to
  three times; a run that still has no usable plan stops instead of spending a GPU hour
  on an issue title
- This mattered more than it looks: an empty spec declares no files, so the scope gate
  checks nothing — the gate switched itself off exactly when it was most needed

**Coherence**
- `devfactory models --sync` now delegates to the same `ensure_models_available()` the
  pipeline calls. There were two implementations of "pull what the registry declares";
  the one that drifted would have been the one nobody ran that day
- Removed the `_score_entry` branch that documented its own uselessness ("kept to not
  affect the function signature but won't be called")
- `git_ops.workspace_path` and `git_ops.default_branch` are public. They were private and
  three modules imported them anyway — an underscore everyone ignores documents nothing

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
