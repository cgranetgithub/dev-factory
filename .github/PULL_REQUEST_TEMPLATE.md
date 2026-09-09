<!--
  This template is for human contributors. DevFactory's own pipeline writes its PR
  body directly (see devfactory/github/pr.py) and does not go through this file.
-->

## What

<!-- What changed, in a sentence or two. -->

## Why

<!-- The problem this solves, or the request behind it. -->

Closes #

## How it was verified

```bash
ruff check .
ruff format --check .
python -m pytest -q
```

<!-- Paste the actual output if any of the above found something worth noting, or
     if verification needed anything beyond these three commands. -->

## Checklist

- [ ] I ran `pre-commit install` (see CONTRIBUTING.md) so lint/format run on every commit
- [ ] The three commands above pass locally
