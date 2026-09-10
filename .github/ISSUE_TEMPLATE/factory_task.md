---
name: Factory task
about: A request for the DevFactory pipeline to build — bug fix, feature, or cleanup.
title: ""
labels: []
---

<!--
  This issue is read by an AI analyst agent, not only by a human. A vague request
  is fine — the analyst reads the codebase and turns it into a specification — but
  precise, testable acceptance criteria produce a better spec and a faster review.
  The more of this template you fill in, the less the analyst has to guess.

  Do not add the `ready-for-dev` label yourself — it triggers the pipeline. The
  repository owner adds it once the issue is ready to be picked up.

  Delete this comment block before submitting; it will not render, but there is no
  reason to keep it in the issue body.
-->

## Problem

<!-- What is wrong, or what is wanted. One or two paragraphs is enough — say what
     you observe or need, not how to fix it. -->

## What to change

<!-- Optional. The analyst can find the relevant files by reading the codebase, but
     if you already know which files or functions are involved, say so — it saves
     a round trip. Delete this section if you don't know. -->

## Acceptance criteria

<!-- A checkbox list. Each line should be an observable, testable outcome — not
     "should work well" but something a test can assert. For example:
     - [ ] `slugify("Café")` returns `"cafe"`
     - [ ] `devfactory run --issue 42` exits 0 when the repo has no open PR for it
-->

- [ ]
- [ ]

## Scope

<!-- Optional. Files or areas that must NOT be touched, if that matters here.
     Delete this section if there's no constraint. -->

## Definition of done

- [ ] `ruff check . && ruff format --check . && python -m pytest -q` passes
- [ ] A test asserts the behaviour described above
