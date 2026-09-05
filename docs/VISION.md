# Vision — the auditable factory

> Where DevFactory is going, and why the destination is *compliance*, not just speed.

DevFactory started as a way to turn GitHub issues into Pull Requests with local models.
The long-term goal is narrower and more useful than "an AI that writes code":

**A local, autonomous factory that develops software end-to-end from an issue, while
enforcing an SDLC precise enough to be audited against recognised standards.**

Two properties are non-negotiable, because together they are the reason to build this
rather than buy a cloud agent:

- **Local-first** — the code, the prompts and the model weights never leave the premises.
- **Auditable** — every step leaves a stored, timestamped, linkable record.

Cloud-first agent products (Devin, OpenHands, Copilot Workspace) optimise raw capability.
For regulated or IP-sensitive clients — medical, defence, finance, public sector —
"nothing leaves my network" and "the whole chain is provable" are buying conditions, not
features. That is the space DevFactory targets.

---

## Compliance targets

Two lenses, adopted in sequence. They govern different things, which is what keeps the
roadmap honest.

### Lens A — the factory (now)

**SOC 2 · ISO/IEC 27001** — information-security assurance about *the system that
produces the software*. It asks: is the factory itself run under control?

- Access control and separation of duties
- Audit logging of every change
- Change and configuration management
- Data residency and secrets handling

### Lens B — the product (next)

**IEC 62304 · ISO 13485 · ISO 14971** — a defined software lifecycle for a medical
device, inside a quality management system. It asks: was this software engineered
through a controlled, traceable process?

- Requirement → test verification traceability
- Software safety classification (Class A / B / C)
- Risk management (ISO 14971)
- Problem resolution and the Design History File

### The through-line

Both reduce to the same three things: **traceability + immutable evidence + explicit
controls**. Build that spine once for the infosec lens, and the medical lens becomes an
*uplift* — safety classification, risk links, tool validation added on top — rather than
a rebuild. Infosec goes first because it is reachable with today's pipeline.

---

## What the pipeline already provides

DevFactory was not designed for compliance, yet several controls an auditor looks for
already fall out of its shape. The gap is evidence and formalisation, not a new machine.

| Control area | What exists today | Status | What to add |
|---|---|---|---|
| Staged, controlled process | Analyst → Developer → Verification → Reviewer ×2 → PR | Partial | Explicit phase gates with a defined exit condition and a stored artifact per stage |
| Audit trail | KB (SQLite) logs each execution: model, duration, tokens, verdicts | Partial | Append-only, hash-chained records: prompt + output + verdict + timestamp + git SHA |
| Change management | Branch + PR + CODEOWNERS, protected `main`, human merge | Have | Link each change back to its issue and approval record |
| Separation of duties | The reviewer cannot approve its own PR (GitHub returns 422) | Have | Document it as a control; keep an independent, competent human approver |
| Human sign-off | The repository owner is the sole merger — the accountable authority | Partial | Capture who approved what, when, against which criteria |
| Data residency | 100% local models via Ollama — nothing sent to a third party | Have | Write it up; it is the confidentiality and IP story |
| Requirement traceability | `TaskSpec.acceptance_criteria` extracted from the issue | Build | Verified links: issue → requirement → code → test → verification result |
| Risk management | — | Build | ISO 14971 risk register, per-change classification, linked to requirements |

The reviewer 422 deserves a note: an agent posting as the PR author cannot formally
approve that PR. That is not a bug to work around — it is a *separation-of-duties
control*. It should be documented and preserved.

---

## The traceability spine

Each link produces a stored, timestamped artifact and points to the next, so the finished
record reads forwards as a lifecycle and backwards as proof. The clause tags show where a
single link satisfies both lenses at once.

```
Issue          ISO 27001 A.8.32 · 62304 §5.1        AI + human
  ↓
Requirement    62304 §5.2 · IEC 62366 usability     AI draft
  ↓
Risk           ISO 14971 · 62304 §7                 AI + human
  ↓
Design         62304 §5.3–5.4                       AI draft
  ↓
Code           62304 §5.5 · ISO 27001 A.8.28        AI (opencode backend)
  ↓
Test           62304 §5.6–5.7                       AI + verification gate
  ↓
Verification   62304 §5.7 · SOC 2 CC8.1             Docker verification
  ↓
Review & sign-off  ISO 13485 §7.3.5 · A.8.32        Human authority
  ↓
Release        62304 §5.8 · DHF record              Recorded
```

---

## The crux — authority, not automation

An AI may *draft* a regulated change. A competent human must *dispose* of it. No standard
asks who typed the code; they ask who is **accountable**, and whether the process caught
the error.

That is what makes AI-authored regulated code defensible to an auditor. It also has a
direct consequence: DevFactory itself becomes a **software tool used in the quality
system** (ISO 13485 §7.5.6). Its intended use, known limitations and verification must be
documented — a tool-validation dossier — and mandatory independent human review is the
control that bounds its failure modes.

**Autonomy scales down as safety class scales up.**

---

## Verifying the controls — a declared control is not a control

Every control above lives in a configuration that someone can change. Claiming
"the reviewer cannot approve its own PR" is worthless unless we can show the guard rails
were actually in place *for the whole period*, and that nobody quietly removed one.

### What can and cannot drift

| Layer | Example | Can it drift? |
|---|---|---|
| Platform invariant | GitHub refuses `APPROVE` / `REQUEST_CHANGES` on your own PR | **No** — hard-coded, not a setting; no org admin can disable it. But GitHub exposes no API field asserting it either, so it is evidenced by vendor documentation plus our own negative test. |
| Repository configuration | Ruleset on `main`, required approvals, CODEOWNERS, bypass list, force-push protection, required status checks | **Yes** — anyone with admin can change it silently |
| Identity & permissions | Who is a collaborator, at which role; whether the bot is admin | **Yes** |

We do not need to *own* GitHub. We need the control configured, verified on a schedule,
and every verification stored as evidence. SOC 2 Type II and ISO 27001 (clause 9.1,
A.5.35 / A.5.36) do not ask "is the control on today" — they ask **"did it operate
throughout the period"**. A screenshot proves nothing; a timestamped, archived,
automated check proves a lot.

### Baseline (repo `cgranetgithub/dev-factory`, read 2026-09-04 18:20 CEST)

| Setting | Value | Assessment |
|---|---|---|
| Ruleset `PR`, enforcement | `active` on `~DEFAULT_BRANCH` | OK |
| `required_approving_review_count` | `1` | OK |
| `require_code_owner_review` | `true` | OK |
| `dismiss_stale_reviews_on_push` | `true` | OK — an approval no longer survives a later push |
| `require_last_push_approval` | `true` | OK — the most recent reviewable push must be approved |
| `deletion`, `non_fast_forward` | enforced | OK — no force-push, no branch deletion |
| `bypass_actors` | `null` | OK — nobody bypasses, including the owner |
| Collaborators | owner = admin, `bot-bobby` = write | OK — the bot cannot alter the ruleset |
| `required_status_checks` | none | **Gap** — the CI workflow now exists but is not yet enforced by the ruleset |

The first two settings were `false` earlier the same day, which mattered because the
factory pushes as `bot-bobby` *after* a human approval can already exist on the PR, with
auto-merge armed. Closing such gaps is repository-owner work: the bot has `write` by
design and cannot change a ruleset.

**This table went stale within thirty minutes of being written.** That is the argument for
`devfactory controls check` rather than a hand-maintained baseline: a control statement
that a human has to remember to update is not evidence.

### Independent verification

`.github/workflows/ci.yml` re-runs ruff, mypy, bandit and pytest on GitHub's
infrastructure. The factory runs the same four tools in its own container, but that runner
is part of the audited process — it decides for itself whether its own output is
acceptable. Making the CI job a **required status check** on the ruleset moves the verification step
claim from the audited party to the platform. The workflow is the prerequisite; wiring it
into the ruleset closes the last gap above.

### The mechanism to build

A `devfactory controls check` command that:

- **snapshots** the enforced configuration through the API — ruleset and its rules,
  `bypass_actors`, collaborators and their roles, CODEOWNERS content, required status checks;
- **compares** it to an expected policy, versioned in the repository;
- **records** each snapshot as an append-only, hash-chained, timestamped entry in the KB —
  that series *is* the evidence of continuous operation;
- **diffs** against the previous snapshot and raises any drift as a recorded event;
- runs **at the start of every pipeline run and on a schedule** — otherwise evidence only
  exists on days when work happened, and the gaps are visible to an auditor;
- optionally **fails closed**: if the policy is not met, the pipeline refuses to open a PR.
  That is the stronger control, and it is a policy decision per project.

**Attribution limit, stated up front:** GitHub's `audit-log` API is Enterprise Cloud only.
On a personal repository we can detect *that* a control changed and *when* (to the
resolution of our polling interval), but not *by whom*. Clients needing attribution must
host in an Enterprise org. Failing closed partly compensates for the missing attribution.

---

## Roadmap

Four phases. Each ships evidence an auditor can read, not only features.

### P0 — Infosec foundation *(now)*

Run the existing factory as a controlled, evidenced system.
*Target: SOC 2 (Security) · ISO 27001 ISMS.*

- Access control and secrets review; documented local-only data-residency posture
- Formalise change management (already: branch → PR → CODEOWNERS → protected `main`)
- Audit-grade run logs: every agent action captured immutably
- **Control monitoring** — `devfactory controls check`: snapshot the enforced GitHub
  configuration, compare it to a versioned expected policy, archive every check, alert on
  drift, run it per pipeline run *and* on a schedule
- ~~Close the baseline gaps `dismiss_stale_reviews_on_push` and
  `require_last_push_approval`~~ *(done 2026-09-04)*; make the CI job a required status
  check to close the last one
- Written policies: change management, access, incident response, model/vendor register

**Exit evidence:** a change-management control operating with a complete, tamper-evident
log, plus an unbroken series of control-verification records covering the audit period.

### P1 — The traceability spine *(next)*

Make every artifact linked, queryable and exportable. This is the shared backbone that
unlocks both lenses.

- Entity model: Issue · Requirement · Design · CodeUnit · Test · Verification · Review · Release
- Append-only, hash-chained record store (extend the KB)
- One-click audit-package export: traceability matrix + evidence per change
- Bidirectional links verified automatically before a PR can be opened

**Exit evidence:** a traceability matrix generated from real pipeline runs.

### P2 — Gates, roles and sign-off

Turn implicit stages into enforced, recorded controls.
*Target: SOC 2 CC-series · ISO 27001 Annex A.*

- Explicit phase gates with defined exit criteria; blocked progress is recorded, not silent
- Recorded human approvals (who / what / when / against which criteria)
- Separation of duties enforced and documented (reviewer ≠ author)
- Per-project policy: SDLC shape, safety posture, permitted model backend

**Exit evidence:** a run that cannot reach PR without a passing gate at each stage.

### P3 — Medical uplift

Add the medical-specific processes on top of the spine.
*Target: IEC 62304 · ISO 13485 · ISO 14971.*

- Software safety classification (Class A/B/C) per project, driving the required rigour
- Risk-management hook (ISO 14971): register, per-change analysis, linked to requirements
- Problem-resolution process (IEC 62304 §9) wired to the KB
- Tool-validation dossier for DevFactory itself; automated Design History File export

**Exit evidence:** a Design History File assembled from the factory's own records.

---

## Honest limits

- **Tooling is not a QMS.** IEC 62304 lives inside an ISO 13485 quality system: SOPs,
  training, management responsibility. DevFactory can produce the records the QMS demands;
  the QMS itself is organisational work.
- **Audit-ready is not certified.** Certification is granted by auditors and notified
  bodies. The goal is to make an audit fast and defensible — never to self-certify.
- **The local-capability ceiling is real.** A local 30B model trails frontier models on
  complex multi-file work. The pluggable developer backend lets a stronger model step in
  where a client permits it; otherwise, scope tasks to fit. See
  [`devfactory/models/registry.py`](../devfactory/models/registry.py).
- **Human-in-the-loop is not optional.** For Class B/C software a qualified person must
  verify and approve. Full autonomy is a low-risk-work story.
- **We monitor the platform, we do not own it.** Control drift is *detected*, not
  prevented, unless the pipeline fails closed — and on a non-Enterprise repository the
  change cannot be attributed to a person. Say so before an auditor says it for you.

---

## Open decision

Confirm the first client's exact assurance target — SOC 2 Type I vs Type II, or the
ISO 27001 statement of applicability — so the P0 evidence set can be scoped precisely.
