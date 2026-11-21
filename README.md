<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/ericmhayes/misfire-ci/main/docs/brand/png/misfire-lockup-stacked-dark%402x.png">
    <img alt="Misfire" src="https://raw.githubusercontent.com/ericmhayes/misfire-ci/main/docs/brand/png/misfire-lockup-stacked-light%402x.png" width="300">
  </picture>
</p>

<p align="center"><em>Know why the build misfired.</em></p>

<p align="center">
  <a href="https://github.com/ericmhayes/misfire-ci/actions/workflows/ci.yml"><img src="https://github.com/ericmhayes/misfire-ci/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/misfire-ci/"><img src="https://img.shields.io/pypi/v/misfire-ci?v=011" alt="PyPI"></a>
  <a href="https://pypi.org/project/misfire-ci/"><img src="https://img.shields.io/pypi/pyversions/misfire-ci?v=011" alt="Python"></a>
  <a href="https://github.com/ericmhayes/misfire-ci/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="License: MIT"></a>
</p>

An open-source GitHub Action that, when a build goes red, runs an **agentic AI
investigation** of the failure and posts an **advisory** PR comment — explaining
the root cause and classifying the failure as **real vs. likely flaky**. Advisory
only: it never blocks, merges, or changes code.

**Bring your own key.** Misfire runs on **Claude or OpenAI** (pick with
`--provider`; the OpenAI path also drives OpenAI-compatible endpoints like GitHub
Models / Copilot). It never provides an API key — you set your own, and every
analysis is billed to your account. The local `--no-ai` mode needs no key.

## How it works

On a red build, Misfire runs a tool-using agent over four shared, mockable tools:

- `get_failing_logs` — the failing CI job logs
- `get_pr_diff` — the change under test
- `get_test_history` — past pass/fail rate (powers the real-vs-flaky verdict)
- `post_pr_comment` — the single write action

Each provider is an adapter behind one seam (`misfire/agents/`) — Claude and
OpenAI today, more easily added — so the shared tools and prompt don't change
when you switch provider with `--provider`.

## Usage

Add a workflow that runs **after** your CI workflow and comments on red builds.
Using the decoupled `workflow_run` trigger means Misfire gets a write-scoped
token (so it works on fork PRs) and can never block or slow your CI job:

```yaml
# .github/workflows/misfire.yml
name: Misfire
on:
  workflow_run:
    workflows: ["CI"]        # ← name of the workflow to watch
    types: [completed]
permissions:
  contents: read
  pull-requests: write
  actions: read
jobs:
  explain:
    if: github.event.workflow_run.conclusion == 'failure'
    runs-on: ubuntu-latest
    steps:
      - uses: ericmhayes/misfire-ci@v0
        with:
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
          github-token: ${{ secrets.GITHUB_TOKEN }}
          run-id: ${{ github.event.workflow_run.id }}
```

`pr-number` is optional: when omitted Misfire resolves the PR from the failed
run's head commit via the GitHub API. This works for PRs from forks too (where
`workflow_run.pull_requests` is empty), and it simply no-ops when the run has no
associated PR (e.g. a push straight to the default branch).

Add your `ANTHROPIC_API_KEY` as a repository secret (bring-your-own-key). The
built-in `GITHUB_TOKEN` supplies the pull-request-write / actions-read scopes.

## Local / offline use

Analyze logs straight from a file or stdin — no GitHub, nothing posted. The
result is printed to stdout.

```bash
# Deterministic, no model call, no key (the offline fallback):
misfire analyze --logs ci.log --no-ai
cat ci.log | misfire analyze --logs - --no-ai

# Full agentic diagnosis (needs ANTHROPIC_API_KEY), optionally with the diff:
misfire analyze --logs ci.log --diff change.diff
```

`--no-ai` gives a deterministic rule-based summary (failure category + the key
log excerpt); the AI path runs the same agent and prints the advisory instead of
posting it. Secrets in the logs/diff are scrubbed before anything is sent to the
model.

## Install (development)

```bash
git clone https://github.com/ericmhayes/misfire-ci.git
cd misfire-ci
pip install -e ".[dev]"
```

## License

MIT — see [LICENSE](https://github.com/ericmhayes/misfire-ci/blob/main/LICENSE).
