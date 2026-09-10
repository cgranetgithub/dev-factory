"""
Model registry — defines which local models are available and their roles.
Edit this file to add/remove models as you pull them into Ollama.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModelMeta:
    name: str  # Ollama model name (e.g. "qwen2.5-coder:14b")
    parameters_b: float  # Billion parameters (approx)
    context_k: int  # Context window in K tokens
    roles: list[str]  # Which agent roles this model can play
    # Whether the model actually DRIVES the "opencode" agentic loop — i.e. reads
    # files, edits them and runs commands, rather than describing the change in
    # prose. Stricter than Ollama's "tools" capability flag, which some models
    # advertise while producing zero edits.
    #
    # Measured, not assumed. The qualification task: a small project with two
    # functions and their tests, "add divide(a, b) raising ValueError on zero, add
    # tests in the existing style, then run pytest and ruff yourself and fix what
    # they report". Four criteria checked afterwards from outside the model —
    # function present, tests present, pytest green, ruff clean — over two
    # identical trials (2026-09-06).
    #
    # This flag was WRONG for four models until that measurement. It was first set
    # from runs made while Ollama still used its 4096-token default context: the
    # system prompt plus the tool definitions overflowed, Ollama truncated from the
    # top, the model lost the instructions telling it how to call tools, and
    # answered in prose. That reads exactly like an incapable model. Anything
    # measured before OLLAMA_CONTEXT_LENGTH was raised to 32768 should be
    # re-measured before being believed — opencode's own documentation asks for 64k.
    #
    # Re-qualify with scratchpad/qualify_real.sh-style runs, two trials minimum:
    # one model scored 4/4, then timed out, then 0/4 on the same exercise.
    drives_agentic_loop: bool = False
    # Whether the model, acting as the reviewer, actually REFUSES a change that
    # violates a stated acceptance criterion. It gates the reviewer role the way
    # drives_agentic_loop gates every agentic role, and for the same reason: the
    # review gate is claimed as a control in docs/VISION.md, and a control that
    # cannot refuse is decoration.
    #
    # Measured, not assumed (2026-09-10, issue #101). Two staged changes against
    # bot-bobby/devfactory-sandbox, each run twice per model, the model pinned:
    #
    #   Case 1 — a violated criterion. The spec says slugify("Café Münster")
    #     returns "cafe-munster"; the code uses encode("ascii", "ignore"), which
    #     drops the accents and returns "caf-mnster"; and the tests assert that
    #     wrong result and pass. Everything needed is inside the diff.
    #   Case 2 — a defect one file away. A correct to_ascii() helper is added,
    #     exported and tested, but slugify() is never changed to call it, so the
    #     criterion stays unmet and the new code is dead. The diff looks complete;
    #     the defect is only visible in a file the diff does not touch.
    #
    # This flag records case 1 only. Case 2 was refused correctly by all four
    # models on all eight runs — reading around the diff works. Case 1 is the
    # harder one *for a model*, because a passing test suite that asserts the
    # defect reads as evidence that the criterion is met.
    #
    # Re-run it with:
    #   pytest tests/integration/test_review_gate_refusal.py -m integration -v -s
    refuses_a_violated_criterion: bool = False
    notes: str = ""


# ── Registry ──────────────────────────────────────────────────────────────────
# Add your models here as you pull them. This registry is the source of truth:
# `devfactory models --sync` pulls every model listed here that is missing from
# Ollama, and the router ignores (with an info log) any model not yet pulled.
#
# Policy: 20B parameters minimum, and every model must fit on the 24 GB card
# (RTX 3090 Ti). Roles are assigned by measured capability, not by a model's
# marketing: only models that drive the agentic loop (see drives_agentic_loop)
# are ever selected, and the two that do not are kept here as recorded
# measurements rather than candidates. Four models drive it today, so the
# developer role has redundancy and the reviewer is never forced onto the
# developer's model.
#
# VRAM: Ollama loads one model at a time, so the constraint is per-model, not the
# sum — each entry must fit on 24 GB with room left for the KV cache. Models near
# ~23 GB on disk (e.g. qwen3.6:35b-a3b) are avoided: they leave too little for a
# 32K context. Aim for ≤ ~20 GB on disk.
#
# Note: the "verification" role uses NO model — VerificationAgent runs deterministic Docker tools
# (ruff/mypy/bandit/pytest), it never calls an LLM, so no model declares "verification".
#
# Roles: "analyst", "developer", "reviewer"

# Dedicated coders: developer and reviewer, not analyst — reading a vague request
# and finding where the problem lives is a reasoning task more than a coding one.
_CODING_ROLES = ["developer", "reviewer"]
# General models fast enough to sit in a loop that may run three times behind
# three gates: every role. This is what gives the developer redundancy.
_GENERAL_AND_DEV_ROLES = ["analyst", "developer", "reviewer"]

MODELS: list[ModelMeta] = [
    # ── Coding-specialised (developer + reviewer) ──────────────────────────────
    ModelMeta(
        name="qwen3-coder:30b",
        parameters_b=30,
        context_k=32,
        roles=_CODING_ROLES,
        # Qualified 4/4 on both trials, and by far the fastest: 20s and 21s where
        # the others need 40-670s. Speed matters more than it looks — the loop can
        # run the developer three times per issue, behind two gates.
        drives_agentic_loop=True,
        # Excluded from the review gate for INSTABILITY, like qwen2.5:32b above,
        # not for incapacity. Case 1, four trials: approved (314s, "meets all
        # acceptance criteria" — it had read the file, seen
        # `text.encode("ascii", "ignore")` and called it "normalizing Unicode to
        # ASCII"), unparseable prose (49s), changes_requested naming the defect
        # correctly (356s), approved again (1063s). One refusal in four, and the
        # two approvals were of a change that breaks the criterion. It is not a
        # reading failure — it refused case 2 on both trials, quoting the value
        # the code produces — and the same 49s-to-1063s spread makes the run cost
        # unpredictable. A gate whose refusal is a coin toss is not a gate.
        # Kept for the developer role, where it is the fastest driver we have.
        refuses_a_violated_criterion=False,
        notes="Qwen3-generation code model (MoE). Newest and strongest Qwen coder.",
    ),
    ModelMeta(
        name="devstral:24b",
        parameters_b=24,
        context_k=32,
        roles=_CODING_ROLES,
        # Mistral's agentic coding model. Despite the branding and Ollama's "tools"
        # capability, it replies in prose and emits no tool calls — it explains how
        # to create the file instead of creating it. The only model here that fails
        # even the trivial one-file task, re-checked after the context fix, so this
        # verdict is not the 4096-token artefact that misjudged the others.
        notes="Mistral AI Devstral Small. Tool-capable flag, but prose-only via Ollama.",
    ),
    ModelMeta(
        # Not a dedicated coder: a strong DENSE general model on the coding side.
        # Dense (not MoE) for per-token quality on precise codegen, and a plain
        # instruct model (no reasoning `<think>` blocks).
        # Excluded from the agentic loop for INSTABILITY, not incapacity: on the
        # same exercise it scored 4/4 (269s), then hit the 900s timeout, then 0/4.
        # A model whose result is a coin toss cannot hold a pipeline role.
        name="qwen2.5:32b",
        parameters_b=32,
        context_k=32,
        roles=_CODING_ROLES,
        notes="Qwen2.5 32B dense general model. Unstable under opencode.",
    ),
    # ── General (analyst + reviewer) ───────────────────────────────────────────
    ModelMeta(
        # Name must match `ollama list` exactly. This model has no explicit tag,
        # so Ollama reports it as ":latest"; without that suffix the availability
        # check fails and the router wrongly skips (and `--sync` re-pulls) it.
        name="glm-4.7-flash:latest",
        parameters_b=32,
        context_k=32,
        roles=_GENERAL_AND_DEV_ROLES,
        # Qualified 4/4 on both trials (88s, 42s). Being a general model, it gives
        # the reviewer role an agentic driver that is NOT the developer's model —
        # which is what makes an exploring reviewer possible without collapsing the
        # separation of duties onto a single model.
        drives_agentic_loop=True,
        # Refuses whenever it answers: 2/2 on case 1 and 2/2 on case 2, naming the
        # mechanism ("encode('ascii','ignore') discards non-ASCII characters
        # instead of converting them") and flagging every test that asserts the
        # defect. One of those case-1 refusals was nearly lost: it began with a
        # sentence of preamble before the JSON, which the parser did not accept —
        # fixed in the same change, and the reason for the test in
        # tests/test_reviewer.py.
        #
        # Open reliability question, recorded rather than hidden: two later case-1
        # trials hit the 1800s harness timeout instead of answering, having taken
        # 79-325s earlier the same evening. Another process on the host was using
        # Ollama at the same time, so this may be contention rather than the
        # model. Re-measure on a quiet host before drawing a conclusion; the
        # judgment itself has never been wrong here.
        refuses_a_violated_criterion=True,
        notes="Zhipu GLM-4.7 (flash/local variant). Strong general reasoning.",
    ),
    ModelMeta(
        # Dense 27B rather than a 35b-a3b MoE variant: the MoE weighs ~23 GB on
        # disk, leaving barely ~1 GB of the 24 GB card for the KV cache — too tight
        # at 32K context (spill/OOM risk). 27B dense (~17 GB) keeps a comfortable
        # context margin while staying above the 20B floor.
        name="qwen3.8:27b",
        parameters_b=27,
        context_k=32,
        # Replaces qwen3.6:27b, which was qualified but the slowest of the pool
        # (670s then 153s) and therefore kept out of the developer role. Same size
        # on disk, same 4/4 on both trials, but 42s and 58s — which puts it in the
        # same band as gemma4 and glm-4.7-flash and removes the only objection to
        # it developing. Re-measured on the same task rather than inheriting its
        # predecessor's flag: a version bump is a different model.
        roles=_GENERAL_AND_DEV_ROLES,
        drives_agentic_loop=True,
        # The strongest reviewer measured: 4/4 refusals across both cases, fast
        # (113-242s), and right for the right reason. On case 1 it explained the
        # mechanism — "é (U+00E9) is a single code point, not base+combining, so
        # there is nothing to ignore and it is dropped entirely" — flagged all
        # three tests that assert the defect, and named both criteria it breaks.
        # On case 2 it reported "verified at runtime: slugify('Café Münster')
        # returns 'caf-m-nster'": it ran the code rather than reasoning about it.
        refuses_a_violated_criterion=True,
        notes="Qwen3.8 27B dense. Latest Qwen general model, safe VRAM margin.",
    ),
    ModelMeta(
        name="gemma4:26b",
        parameters_b=26,
        context_k=32,
        roles=_GENERAL_AND_DEV_ROLES,
        # Qualified 4/4 on both trials (64s, 46s), second fastest overall. Works in
        # many small steps (25-35 where others take 9) — a different method, same
        # outcome; step count is not a quality signal.
        drives_agentic_loop=True,
        # Refuses on 4/4 across both cases, 147-389s. The most economical of the
        # three: two or three comments that go straight to the line — "this line
        # removes accented characters instead of normalizing them" and "the
        # expected value 'caf-mnster' is incorrect, the requirement specifies
        # 'cafe-munster'". Repo-relative comment paths on every run, which the
        # inline-comment mapping needs.
        refuses_a_violated_criterion=True,
        notes="Google Gemma 4. Reliable structured output for analyst/reviewer.",
    ),
]

# Index by name for quick lookup
_by_name: dict[str, ModelMeta] = {m.name: m for m in MODELS}


def get_models_for_role(role: str) -> list[ModelMeta]:
    """Return all models that support a given role."""
    return [m for m in MODELS if role in m.roles]


def get_model(name: str) -> ModelMeta | None:
    return _by_name.get(name)
