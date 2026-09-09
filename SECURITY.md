# Security Policy

## Supported versions

There is one line of development: `main`. Security fixes land there. There are no
released versions to backport to yet.

## Reporting a vulnerability

Report privately through GitHub's **Private vulnerability reporting**: go to the
**Security** tab of this repository → **Report a vulnerability**.

> **If the button is missing, the feature is not enabled yet on this repository.**
> Private vulnerability reporting has to be turned on by the maintainer under
> **Settings → Code security → Private vulnerability reporting**. If you don't see it,
> open a public issue titled **"Security contact"** with no details of the
> vulnerability in it, and the maintainer will reach out to arrange a private channel.

Do not open a public issue describing the vulnerability itself, and do not post it
anywhere public (a PR, a discussion, a comment) before it is fixed.

## What is in scope

DevFactory holds a GitHub personal access token with `repo` scope, clones repositories
onto the machine it runs on, reads issue bodies written by third parties and feeds them
to a local model, lets that model generate and run code, and then pushes branches and
opens pull requests with the result. That is a real attack surface, not a formality.
Concrete classes of report we want to hear about:

- **Container escape.** A crafted issue body, or a crafted change the developer agent
  is induced to write, that lets model-generated code escape the verification
  container (`docker/Dockerfile.test`) and act on the host it runs on.
- **Token exfiltration.** Any path by which the `GITHUB_TOKEN` (or another secret in
  `.env`) could leak — through model-generated code, through logs
  (`devfactory logs` / the JSON-lines log file), through the SQLite knowledge base, or
  through the prompt/response sent to the local model.
- **Prompt injection with a real-world effect.** Text inside an issue, a spec issue, or
  a diff that causes an agent to take an unintended git or GitHub action — pushing to
  a branch it shouldn't, opening a PR against an unexpected repository, modifying
  files outside the declared scope, or similar.
- **Anything that lets a change reach a merged state without a human review**, since
  that human review is the control the whole design leans on (see below).

If you're unsure whether something is in scope, report it anyway and let the
maintainer make the call.

## What is out of scope

- Vulnerabilities in the local models themselves, in **Ollama**, **OpenCode**, or
  **Docker** — report those upstream, to the respective project.
- The standing design fact that **a human approves every pull request before it
  merges**. That gate is deliberate (see `docs/VISION.md` — it's called out there as a
  separation-of-duties control), not an oversight, so "the AI can propose a bad
  change" on its own is not a vulnerability; a way to make it merge *without* human
  approval is.

## What to expect

We aim to acknowledge a report within a week. Response times aren't formally
guaranteed beyond that yet — this is a project run by one maintainer — but a private
report will get a private reply, and you'll be credited in the fix unless you ask not
to be.
