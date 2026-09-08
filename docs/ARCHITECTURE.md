# Target architecture

> Decided 2026-09-08. This describes where the pipeline is going, and why.
> `README.md` describes what it does today. Where the two disagree, the README is
> the present tense and this document is the future one.

## Two rules

**1. Never reinvent the wheel.** If a maintained package does what we need, we use
it. This overturns an earlier convention in `CLAUDE.md` that forbade orchestration
frameworks — a rule written when the pipeline was a straight line and no longer
justified now that it has cycles.

**2. Every agent is autonomous.** An agent does not need to know what the previous
one did, only that it is its turn. **Agents pass nothing to each other. Their
outputs are stored**, where the next stage can find them on its own.

The second rule is the one with consequences, so it is worth stating what it
forbids: adding a field to a shared object to carry information from one stage to
the next. If a stage cannot do its job from the issue, the published artifacts and
the codebase, the specification is wrong — not the plumbing.

## What each agent reads

Nothing is handed over. Each reads the world.

| Agent | Reads | Produces | Where it goes |
|---|---|---|---|
| **Analyst** | The request (often one vague line) + the codebase | A workable specification | A **new GitHub issue**, linked to the original |
| **Developer** | The original issue + the spec issue + the codebase | Code, on a branch | Commits |
| **Verification** | The checkout | A pass/fail report | The run record |
| **Reviewer** | The issue, the spec, the branch and the codebase | A verdict and comments | The pull request |

A stage can be re-run on its own. Nothing needs to be replayed to reconstruct what
it should have received.

## The analyst is the one that matters

Its job is **not** to restate an issue that already carries acceptance criteria.
Issue #40 was written by hand with a scope section, acceptance criteria and a
definition of done — it is the best-handled issue so far, and the analyst added
nothing to it but a failure mode.

Its job is to turn *"login breaks when the password contains an accent"* into a
specification someone can implement: read the codebase, find where passwords are
handled, work out what is wrong, and write it down.

**A client will never write an issue like #40.** That is the whole reason this
agent exists, and it is why it needs the codebase — which it has never had.

## Why the spec is an issue, not a comment

| | Comment | Linked issue |
|---|---|---|
| Edits are versioned | poorly | yes |
| A human can amend it before development starts | awkward | yes |
| First-class object (labels, comments, close) | no | yes |
| Appears in the PR diff | no | no |
| Cross-referenced both ways | weak | yes |

It also creates a cheap, optional human checkpoint: reading a specification costs
two minutes, reviewing a pull request costs twenty.

Costs to accept: two issues per task — a `devfactory:spec` label and a filter keep
the list readable — and the artifact lives in GitHub rather than in git, which is
weaker evidence than a commit for an auditor. Recorded in `docs/VISION.md`.

## The harness is a seam

Every agent reaches its model through one module, `devfactory/opencode.py`: an agent
asks for a run with a prompt, a checkout, a model and a read-only flag. It does not
know which CLI answers.

That boundary is deliberate. OpenCode is today's implementation; Claude Code driving
a local model, or Aider, may join it. Rebuilding a harness from scratch is a large
piece of work that has nothing to do with what this project sells, so we will not.

There is no plugin framework, and there should not be one until a second
implementation exists — one does not justify an abstraction. The seam is kept clean
so that widening it later is small.

## Everything runs through OpenCode

Software work needs the code. An agent that only sees an issue is guessing, and we
have the failures to prove it: the analyst declaring filenames it had never seen,
and a reviewer approving dead code three times because the defect was one file
outside the diff.

Measured before committing to it — `gemma4:26b`, read-only, on this repository,
asked which file implements the scope gate and what it would take to add another:

```json
{"scope_gate_file": "devfactory/verification/scope.py",
 "files_to_modify": ["devfactory/orchestrator.py"]}
```

Both correct, in 101 seconds, with **zero files modified**. Three properties hold:

- **Read-only is enforced by OpenCode**, not by us. The `plan` agent denies `edit`
  everywhere except `.opencode/plans/*.md`.
- **Structured output survives.** Stdout carries the answer and nothing else; models
  return a fenced JSON block, which the existing parser already accepts.
- **The cost is bearable.** 101s against the 65s average of today's blind analyst.

What it costs: token accounting is lost for every role, because OpenCode does not
report usage through this interface. The knowledge base never read those columns —
its aggregate query selects durations and scores only — so the loss is diagnostic
rather than functional. It cost us once already: `completion_tokens = 4096`, exactly
the ceiling, is what explained an empty specification. Recovering it means parsing
OpenCode's `--format json` event stream, and that is worth doing before a metered
cloud model joins the pool, where tokens are money.

## LangGraph owns the flow

The flow is not a line. The reviewer sends the change back, verification runs
again, review runs again, and a shared budget has to stop it eventually. That is a
cyclic graph with conditional edges, which is exactly what LangGraph models — and
what our `while True` with three counters approximates by hand.

```
analyst ──► developer ──► scope ──► verification ──► review ──► push ──► PR
               ▲            │            │             │
               └────────────┴────────────┴─────────────┘
                        (send back, shared budget)
```

Under rule 1 we take it rather than maintain our own. v1.2.11; it pulls
`langchain-core` and four sibling packages. We use the graph, not LangChain's model
layer — our models are reached through Ollama and the OpenCode CLI, and that does
not change.

What it buys beyond the loop we already have: **checkpointing**. A run that dies at
minute thirty resumes instead of restarting, and the checkpoint series is a record
of every state transition — which is most of what `docs/VISION.md` P1 asks for.

## The human reviews at the end

Once the code is functional and the gates agree, and not before. The pull request
is the gate, as it is today.

LangGraph's `interrupt` would let a run pause mid-flight for a human decision and
resume days later. We are not using it. But nothing here forecloses it, and the
spec issue already provides an informal early checkpoint: it can be read and
amended before the developer starts.

## What this changes

Every row below has landed (the last, the spec read back from its issue, on
2026-09-08). The table stays as the record of the distance covered.

| Before | Now |
|---|---|
| `PipelineContext` carries 17 fields between stages | Orchestration state only; content lives in issues and artifacts |
| The analyst sees the issue text | The analyst reads the codebase |
| The reviewer sees a diff | The reviewer reads the branch and the codebase |
| The spec exists in RAM until the process exits | The spec is an issue anyone can read, amend and cite |
| A crashed run is lost | A crashed run resumes from its last checkpoint |
| `repo_context.py` hand-builds context for the developer | OpenCode does it |
| The single-shot `ollama` backend | **Removed.** A developer-only fallback protects a step the run never reaches: the analyst needs the harness too |
