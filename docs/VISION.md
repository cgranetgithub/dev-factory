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
| Staged, controlled process | Analyst → Developer → QA → Reviewer ×2 → PR | Partial | Explicit phase gates with a defined exit condition and a stored artifact per stage |
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
Test           62304 §5.6–5.7                       AI + QA gate
  ↓
Verification   62304 §5.7 · SOC 2 CC8.1             Docker QA
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

## Roadmap

Four phases. Each ships evidence an auditor can read, not only features.

### P0 — Infosec foundation *(now)*

Run the existing factory as a controlled, evidenced system.
*Target: SOC 2 (Security) · ISO 27001 ISMS.*

- Access control and secrets review; documented local-only data-residency posture
- Formalise change management (already: branch → PR → CODEOWNERS → protected `main`)
- Audit-grade run logs: every agent action captured immutably
- Written policies: change management, access, incident response, model/vendor register

**Exit evidence:** a change-management control operating with a complete, tamper-evident log.

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

---

## Open decision

Confirm the first client's exact assurance target — SOC 2 Type I vs Type II, or the
ISO 27001 statement of applicability — so the P0 evidence set can be scoped precisely.
