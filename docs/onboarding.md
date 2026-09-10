# Onboarding a repository

> Written from onboarding `cgranetgithub/biz-explore` and `cgranetgithub/news-watch`
> on 2026-09-10 (issue #92). The results of that run are at the bottom, including
> what neither repository passes — which is the point of doing this first.

A repository does not have to know that DevFactory exists. Everything the factory
needs to know about it is either read from the repository itself or recorded here,
in DevFactory. Onboarding is therefore short, and its purpose is to find out
**before** an issue is processed whether the repository can be processed at all.

---

## 1. Run init

```bash
devfactory init --repo owner/repo
```

It does five things, in order:

1. creates the DevFactory labels on the repository;
2. builds the verification image (`docker/Dockerfile.test`);
3. checks Ollama and pulls every model the registry declares;
4. initialises the knowledge base;
5. **runs the gate on the repository's default branch** and reports it tool by tool.

Step 5 decides the outcome: `init` exits non-zero when the gate does not pass,
because a repository whose `main` already fails verification will fail it on every
run, whatever the developer agent produces.

> **The image must be rebuilt after upgrading DevFactory.** Since issue #92 the
> runner drives `uv` inside the image; an older image has no `uv` and every gate
> run ends in the error state. `devfactory init` rebuilds it, or:
> `docker build -f docker/Dockerfile.test -t devfactory-test:latest .`

## 2. Run the gate dry run on its own

The same check, without the rest of init:

```bash
devfactory gate check --repo owner/repo
devfactory gate check --repo owner/repo --path /some/local/checkout   # no clone
```

It clones (or refreshes) the repository at `$DEVFACTORY_WORKSPACE/onboarding/<repo>`
— never in the pipeline's own workspace, so a dry run cannot disturb a run in
progress — and prints one line per tool:

```
         cgranetgithub/news-watch @ a5ac825e (main)
┏━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Tool   ┃ Result     ┃ Detail                              ┃
┡━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ ruff   │ ✗ findings │ 67 finding(s)                       │
│ mypy   │ ✗ findings │ 10 error(s)                         │
│ bandit │ ✗ findings │ 264 finding(s), top severity MEDIUM │
│ pytest │ ✓ pass     │ 210 passed, 0 failed                │
└────────┴────────────┴─────────────────────────────────────┘
python 3.12 (from requires-python >=3.12), install from uv.lock, extras ['test', 'audit'] · 15.3s
```

Read the exit code, which separates the two failures that matter:

| Exit | Meaning | Whose problem |
|---|---|---|
| 0 | the repository passes its own gate | — |
| 1 | a tool ran and found the repository wanting | **the target's** |
| 2 | a tool did not run at all | **ours** (or a declaration missing from the target) |

Exit 2 is never reported as "nothing wrong": a gate that could not look has not
approved anything. The same convention as `devfactory controls check`.

## 3. Add a verification profile, only if you must

The gate derives its environment from the repository. What it derives:

| | Default |
|---|---|
| Python | the profile → `.python-version` → the lowest version satisfying `requires-python` → **3.12** |
| Install, with `uv.lock` | `uv sync --frozen --python <v> [--extra …]` |
| Install, with `pyproject.toml` | `uv pip install -e .[extras]` |
| Install, with `requirements.txt` | `uv pip install -r requirements.txt [-r requirements-dev.txt] [-r requirements-test.txt]` |
| Install, with none of them | nothing installed; the virtualenv is still created |
| Extras | the optional-dependency groups named `dev`, `test`, `tests`, `testing` |
| Tools | ruff, mypy, bandit, pytest |
| pytest | always from the target's own environment; added there only if the target ships none |

So the common case needs **no entry anywhere**. When it does, the entry goes in
[`profiles/verification.toml`](../profiles/verification.toml), keyed by
`owner/repo` — in DevFactory, never in the target. Every field is optional:

```toml
["owner/repo"]
python_version = "3.14"
install = ["uv sync --all-extras"]   # replaces the derived install
extras = ["test", "audit"]           # replaces the derived extras
tools = ["ruff", "pytest"]           # narrows the gate
ruff_args = ["--select", "E,F,B"]
mypy_args = ["--strict"]
pytest_args = ["-x"]
```

An unknown key, an unknown tool or a version that is not one is refused when the
file is read — a gate configured by accident cannot produce evidence.

**A tool left out of `tools` is reported as skipped, in the log, in the summary
and in the pull request.** It never reads as a pass. Use it to record a decision,
not to make a red gate green: whether a pre-existing finding is acceptable is the
target owner's judgement, made in the target.

The two entries that exist today, and why each is needed:

- **`cgranetgithub/news-watch`** — `extras = ["test", "audit"]`. Its own suite
  imports `anthropic`, which the project deliberately keeps in an optional `audit`
  extra so that a default install has no cloud dependency. No convention reads
  "audit" as a test extra, so the derived set misses it and nine tests fail on
  `ModuleNotFoundError` — a failure of the gate's setup, reported as a failure of
  their suite. One line fixes it.
- **`cgranetgithub/biz-explore`** — `python_version = "3.14"`. It declares no
  interpreter at all (`requirements.txt`, no `pyproject.toml`) and its CI installs
  3.14 via uv. The derived default (3.12) installs and tests cleanly there, but a
  gate that judges a repository on an interpreter its CI never uses can pass a
  change CI then rejects.

Neither entry narrows the gate.

## 4. Know what the agents will read in the target

Every agent runs through OpenCode **in the target's checkout**, so the target's
own instructions apply to them:

- `CLAUDE.md`, `AGENTS.md`, `.claude/` — read as project instructions;
- the target's `ruff.toml` / `pyproject.toml` configuration — the gate runs the
  tools from the repository root, so the repository's own configuration governs
  them.

Both current targets mandate **French comments**. DevFactory's own style rule
(English everywhere) does *not* travel with the agents: **an agent working in a
target follows the target's conventions**, which is what a contributor would do,
and what makes the resulting pull request reviewable by its owner. Nothing needs
configuring for this — it is a consequence of the agents reading the repository —
but it is worth checking on the first run that the developer agent did follow
them.

## 5. Expect the same branch protection as on DevFactory

The factory's separation of duties is enforced by the platform, not by the
pipeline. On the target:

- pull requests required on the default branch, no direct pushes;
- at least one approving review, and the bot account must not be able to approve
  its own pull request — the reviewer agent's inability to approve is a control,
  not a bug;
- `CODEOWNERS` so the owner is asked;
- the bot account (`bot-bobby`) a collaborator with write access, and not in any
  bypass list.

Record and monitor it:

```bash
devfactory controls check --repo owner/repo
```

The first run stores a baseline; later runs report drift. See `docs/VISION.md`,
"Verifying the controls".

---

## What onboarding the two real repositories found

Gate on the default branch, two runs each, image already built. `main` at
`9a165fb9` (biz-explore) and `a5ac825e` (news-watch); both runs identical.
For reference, the same gate on DevFactory itself passes in 17.4 s / 19.2 s,
against 22.2 s / 22.3 s when it installed with `pip` (issue #77).

| | biz-explore | news-watch |
|---|---|---|
| Environment | python 3.14 (profile), `requirements.txt` + `requirements-dev.txt` | python 3.12 (`requires-python >=3.12`), `uv.lock`, extras `test`, `audit` |
| ruff | **pass** — 0 findings | **fail** — 67 findings |
| mypy | **fail** — 24 errors | **fail** — 10 errors |
| bandit | **fail** — 338 findings, top MEDIUM (20 × B608 in `storage/db.py`) | **fail** — 264 findings, top MEDIUM (2 × B608 in `src/`) |
| pytest | **pass** — 188 passed | **pass** — 210 passed |
| Gate wall clock | 17.7 s / 17.9 s | 15.1 s / 14.4 s |
| Verdict | exit 1 — the target's own gate fails | exit 1 — the target's own gate fails |

Every one of those failures is the target's, and none of them is new: mypy had
never run on either repository, and bandit's MEDIUM findings are SQL built by
string concatenation that predates the factory. Two are worth singling out:

- **news-watch has a real bug in `src/main.py:112`** — ruff `F821` and mypy
  `name-defined` both report `force` as undefined. The gate found it on its first
  run.
- **news-watch declares no ruff configuration**, so the gate applies ruff's
  defaults, which are much broader than the `E,F,B` biz-explore selects. 67
  findings is mostly that. The fix is a `ruff.toml` in news-watch, chosen by its
  owner; `ruff_args` in the profile is the fallback, and it is a fallback.

**Neither repository can be processed by the factory until its `main` passes.**
The developer agent would spend its whole retry budget on findings that have
nothing to do with the issue it was given, and the run would end with no pull
request. That is a real limitation of an absolute gate, recorded below and tracked in
[#104](https://github.com/cgranetgithub/dev-factory/issues/104).

---

## Known limits

- **The gate is absolute, not differential.** It asks "is this repository clean?",
  not "did this change make it worse". Any repository with pre-existing findings
  is unusable until they are fixed or a tool is switched off for it. This is the
  single biggest obstacle to onboarding an existing codebase, and it is why both
  current targets are onboarded but blocked; tracked in
  [#104](https://github.com/cgranetgithub/dev-factory/issues/104).
- **ruff's defaults stand in for a missing configuration.** A target with no ruff
  configuration is judged by whatever the image's ruff version defaults to, which
  changes when the image is rebuilt. Targets should carry their own config.
- **No shared uv cache.** Each container fetches its wheels again; the
  interpreters are baked into the image, the packages are not. That costs a few
  seconds per run today — the whole gate is 14–19 s on these repositories,
  DevFactory included — and would cost more on a heavier dependency tree.
- **A Python version the image does not cache is downloaded per run.** Cached:
  3.11, 3.12, 3.13, 3.14.
- **The install needs the network**, like `pip install` did before it. Nothing
  about the *code* leaves the machine: no pipeline step sends the checkout, a
  prompt or a diff anywhere.
- **Large tracked directories would be copied per run.** The gate copies the
  checkout to `/build` before installing into it (the mount is read-only).
  Neither target pays for this: biz-explore ignores its 1.3 GB `data/`, and
  news-watch's checkout is 1.2 MB — it tracks no `data/` at all, contrary to what
  issue #92 assumed. A repository that tracked a gigabyte would pay it on every
  gate run.
