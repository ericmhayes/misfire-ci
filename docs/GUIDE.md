# Misfire — User Guide

*Know why the build misfired.*

A task-oriented guide to Misfire: a GitHub Action (and CLI) that, when a build
goes red, runs an agentic AI investigation of the failure and posts an
**advisory** PR comment — explaining the root cause and classifying the failure
as **real vs. likely flaky**. Advisory only: it never blocks, merges, or changes
code. This is the source of truth for how the tool works today.

**Contents**
1. [Install](#1-install)
2. [Core concepts](#2-core-concepts)
3. [Quickstart — the GitHub Action](#3-quickstart--the-github-action)
4. [How an investigation works](#4-how-an-investigation-works)
5. [The advisory comment](#5-the-advisory-comment)
6. [Real-vs-flaky classification](#6-real-vs-flaky-classification)
7. [Local / offline use (`misfire analyze`)](#7-local--offline-use-misfire-analyze)
8. [CLI reference](#8-cli-reference)
9. [GitHub Action reference](#9-github-action-reference)
10. [Cost & safety controls](#10-cost--safety-controls)
11. [Security](#11-security)
12. [Providers, models & endpoints](#12-providers-models--endpoints)
13. [How Misfire compares](#13-how-misfire-compares)
14. [Troubleshooting & FAQ](#14-troubleshooting--faq)
15. [Slack notifications](#15-slack-notifications)

---

## 1. Install

Misfire ships as the PyPI package **`misfire-ci`** (CLI command: `misfire`),
MIT-licensed, **bring-your-own-key** (you supply your provider's API key; Misfire
never provides one). Requires **Python 3.10+**.

```bash
pip install misfire-ci              # core (Claude provider)
pip install "misfire-ci[openai]"    # add the OpenAI provider / OpenAI-compatible endpoints
```

For development (tests, linting):

```bash
git clone https://github.com/ericmhayes/misfire-ci.git
cd misfire-ci
pip install -e ".[dev]"
```

You do **not** need to install anything to use Misfire as a GitHub Action — the
Action installs `misfire-ci` for you (see [§3](#3-quickstart--the-github-action)).

---

## 2. Core concepts

**Advisory, never blocking.** Misfire's only write action is a single PR comment.
It never merges, blocks, quarantines, or edits code. If Misfire has a bug, your CI
is unaffected. This is a hard design rule, not a default.

**Agentic, not a single prompt.** Misfire runs a tool-using agent that decides
*which* evidence to gather and in what order, over four shared, mockable tools:

| Tool | What it does |
|------|--------------|
| `get_failing_logs` | the failing CI job logs (truncated, secret-scrubbed) |
| `get_pr_diff` | the change under test (secret-scrubbed) |
| `get_test_history` | how often the failing test failed on recent, *unrelated* runs |
| `post_pr_comment` | the single write action — posts/updates one advisory |

**Real vs. flaky.** Beyond explaining *why* the build failed, Misfire
classifies a failing test as **real** vs. **likely flaky** using a deterministic
statistics layer over run history — a number the model is told to treat as
authoritative and **cannot fabricate**. See [§6](#6-real-vs-flaky-classification).

**Multiple providers, your key.** Misfire runs on **Claude or OpenAI** — pick with
`--provider` (default `claude`), and the OpenAI provider also drives any
OpenAI-compatible endpoint (e.g. GitHub Models / Copilot). You bring your own key
for the chosen provider; Misfire never provides one. Each provider is an adapter
behind a seam in `misfire/agents/`, so adding one is easy. See
[§12](#12-providers-models--endpoints).

---

## 3. Quickstart — the GitHub Action

Add a workflow that runs **after** your CI workflow completes. Using the
decoupled `workflow_run` trigger means Misfire gets a write-scoped token (so it
works on fork PRs) and can never block or slow your actual CI job.

```yaml
# .github/workflows/misfire.yml
name: Misfire
on:
  workflow_run:
    workflows: ["CI"]        # ← the name of the workflow to watch
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

Add **your own** provider key as a repository secret (`ANTHROPIC_API_KEY` for the
default Claude provider) — Misfire never supplies a key. The built-in
`GITHUB_TOKEN` supplies the pull-request-write / actions-read scopes. To run on
OpenAI or an OpenAI-compatible endpoint (GitHub Models / Copilot), set the
`provider` / `model` inputs; see [§12](#12-providers-models--endpoints).

**Why no `pr-number`?** It's optional — when omitted, Misfire resolves the PR
from the failed run's head commit via the GitHub API. This works for PRs from
forks too (where `workflow_run.pull_requests` is empty) and simply no-ops when a
red run has no associated PR (e.g. a direct push to the default branch).

---

## 4. How an investigation works

On a red build, the agent loop runs end-to-end, fully offline from your code (it
only reads logs/diff/history and writes one comment):

```
 workflow_run: CI completed == failure
        │  misfire explain --run <id>      (action.yml)
        ▼
 resolve the PR from the run's head commit (if --pr omitted)
        ▼
 agent loop (bounded by a turn cap + token budget):
   ├─ get_failing_logs → download the FAILING jobs' logs → truncate → scrub secrets
   ├─ get_pr_diff      → unified diff → scrub secrets
   ├─ get_test_history → sample recent runs → deterministic flaky verdict
   └─ post_pr_comment  → render structured Verdict → upsert ONE PR comment
        ▼
 advisory comment on the PR (root cause + category + confidence + flaky verdict + evidence)
```

The agent chooses its path: a compile error rarely needs the diff read line by
line; a test assertion usually does; only a failing *test* triggers a history
lookup. The model supplies **structured fields** (root cause, category,
confidence, optional fix, implicated test id) — Misfire renders the comment body
and computes the flaky verdict itself.

---

## 5. The advisory comment

Misfire renders the comment deterministically from the model's structured fields
— the model does not write raw Markdown. A typical comment:

> ### 🔧 Misfire — build failure analysis
>
> **Root cause:** `auth.py:42` changed the status from `active` to `pending`, so
> `test_auth_status` now fails its assertion.
> **Category:** `test_assertion`
> **Confidence:** 90%
>
> **Verdict:** 🔴 Likely a real failure (95%) — passed on every recent unrelated
> run and failed only on the commit under test.
>
> **Suggested fix:** revert the status change in `auth.py:42` or update the test
> if `pending` is intended.
>
> <details><summary>Evidence</summary>
>
> - Failing run: https://github.com/you/repo/actions/runs/1472
> - History: sampled 20 recent run(s) — 1 failed, 0 on other branches.
> </details>
>
> ---
> <sub>🔧 Posted by Misfire — advisory only, nothing was changed or blocked.</sub>

**Evidence block.** Every advisory carries a collapsed, **Misfire-authored**
Evidence section — a link to the failing run and the raw history counts the flaky
verdict was computed from (runs sampled, failures, failures on other branches,
same-commit retries). None of it is model-authored, so you can verify the call
rather than take it on faith.

**One rolling comment per PR.** Each advisory carries a hidden HTML marker
(`<!-- misfire:advisory -->`). On the next red run Misfire finds that comment and
**updates it in place** rather than posting a new one, so a PR never accumulates a
pile of Misfire comments.

**Fields:**

| Field | Source | Notes |
|-------|--------|-------|
| Root cause | model | sanitized (HTML-escaped, links/images neutralized, length-capped) |
| Category | model | one of `test_assertion`, `compile_error`, `dependency`, `timeout`, `infrastructure`, `unknown` |
| Confidence | model | self-reported diagnosis confidence (0–100%) |
| Verdict | **deterministic** | real-vs-flaky from history; only shown when a test is implicated |
| Suggested fix | model | optional; only when the agent is reasonably confident |
| Evidence | **deterministic** | run link + the history counts behind the verdict; verifiable, never model-authored |

---

## 6. Real-vs-flaky classification

The headline feature. When a specific test fails, Misfire asks: *does this test
also fail on commits other than the one under review?* If so, it's failing
independently of this PR — **likely flaky**, not a regression this change
introduced.

**It is deterministic, not an LLM guess.** The verdict is computed by plain-Python
statistics over the test's recent history (`misfire/flaky.py`), and it is
recomputed from history **at post time** — the model can read it and explain it,
but it cannot state or override it.

| Classification | Meaning |
|----------------|---------|
| 🔴 **Real** | failed only on the commit under test; passed on recent unrelated runs |
| 🟡 **Likely flaky** | also failed on commits other than the one under test |
| ⚪ **Unknown** | not enough history to judge (never guessed as "real") |

Confidence is bounded (0.50–0.95) and scaled down for small samples. A single
failure on an unrelated commit is enough to *lean* flaky; confidence rises with
how often it fails elsewhere and how much history backs the call.

**Pass-on-retry — the strongest signal.** If a test failed on one attempt and
then **passed on a re-run of the same commit**, the code didn't change between
attempts, so a red→green flip is near-proof the failure is non-deterministic.
Misfire detects this (by comparing a prior attempt's logs against a succeeded
re-run) and classifies it **likely flaky** with high confidence — outranking the
cross-commit signal.

**Works beyond pytest.** The failure-marker detection recognizes pytest, Go
(`--- FAIL`), Jest/Mocha/Vitest (`✕`), and TAP (`not ok`), so the flaky verdict
holds for non-Python test suites rather than silently defaulting to "real".

**Where history comes from.** Today the provider
(`GitHubTestHistoryProvider`) reads history on demand from the GitHub REST API —
it samples a bounded window of recent runs of the same workflow and scans each
run's logs for the test id. There's no persistent store to operate. The provider
sits behind a swappable `TestHistoryProvider` interface, so another backend could
drop in without touching the tools, prompt, or classifier.

---

## 7. Local / offline use (`misfire analyze`)

Analyze logs straight from a file or stdin — no GitHub, nothing posted. The
result is printed to stdout.

```bash
# Deterministic, no model call, no key (the offline fallback):
misfire analyze --logs ci.log --no-ai
cat ci.log | misfire analyze --logs - --no-ai

# Full agentic diagnosis (needs ANTHROPIC_API_KEY), optionally with the diff:
misfire analyze --logs ci.log --diff change.diff
```

- **`--no-ai`** produces a deterministic, rule-based summary: a heuristic failure
  category plus the key log excerpt (the failing tail + first error block). No
  model, no key, no network — useful as a fast triage step or when the LLM is
  unavailable.
- **Default (AI)** runs the same agent over the provided text and prints the
  rendered advisory instead of posting it. The flaky verdict is `unknown` offline
  (there is no run history to consult). Secrets in the logs/diff are scrubbed
  before anything is sent to the model.

**Machine-readable output.** Add `--format json` to emit the verdict as JSON
(`root_cause`, `category`, `confidence`, `classification`, `suggested_fix`)
instead of Markdown — handy for piping into other tooling. With `--no-ai` the JSON
carries the heuristic `category` (other fields `null`).

```bash
misfire analyze --logs ci.log --format json
misfire analyze --logs ci.log --no-ai --format json | jq .category
```

---

## 8. CLI reference

```
misfire explain --run <id> [--pr <n>] [--provider <p>] [--model <m>] [--max-turns <n>] [--token-budget <n>]
```
The GitHub path. Fetches the failing logs + diff for `run_id`, investigates, and
posts/updates the advisory on the PR. `--pr` is resolved from the run's head
commit when omitted. Requires `GITHUB_TOKEN` and `GITHUB_REPOSITORY` in the
environment (set automatically inside the Action) and your provider's API key
(default `ANTHROPIC_API_KEY`).

```
misfire analyze --logs <file|-> [--diff <file>] [--no-ai] [--format text|json] [--provider <p>] [--model <m>] [...]
```
The local path. Reads logs from a file or stdin (`-`); no GitHub, nothing posted.
`--no-ai` gives the deterministic summary (no key needed). See [§7](#7-local--offline-use-misfire-analyze).

| Flag | Applies to | Meaning |
|------|-----------|---------|
| `--run` | explain | failed workflow run id (required) |
| `--pr` | explain | PR number; resolved from head commit if omitted |
| `--provider` | both (AI) | `claude` or `openai` (default `claude`); bring your own key |
| `--model` | both (AI) | model to use; falls back to the provider's default |
| `--logs` | analyze | path to a log file, or `-` for stdin (required) |
| `--diff` | analyze | optional path to a unified-diff file |
| `--no-ai` | analyze | deterministic summary, no model call |
| `--format` | analyze | `text` (default) or `json` (machine-readable verdict) |
| `--max-turns` | both (AI) | cap on agent model round-trips (default 12) |
| `--token-budget` | both (AI) | cap on cumulative input+output tokens (default 200,000) |

---

## 9. GitHub Action reference

**Inputs** (`action.yml`):

| Input | Required | Default / notes |
|-------|----------|-----------------|
| `provider` | no | `claude` (default) or `openai`. Bring your own key |
| `anthropic-api-key` | for `claude` | your Anthropic key (BYO-key; Misfire never provides one) |
| `openai-api-key` | for `openai` | your OpenAI key (or GitHub token for GitHub Models / Copilot) |
| `openai-base-url` | no | OpenAI-compatible endpoint (e.g. GitHub Models / Copilot) |
| `model` | no | you set the model; falls back to the provider's default |
| `github-token` | yes | needs PR-write + actions-read (the built-in `GITHUB_TOKEN` works) |
| `run-id` | no | defaults to the triggering `workflow_run.id`, then `github.run_id` |
| `pr-number` | no | resolved from the run's head commit when omitted (fork-safe) |
| `max-turns` | no | override the agent turn cap |
| `token-budget` | no | override the agent token budget |

**Outputs:**

| Output | Value |
|--------|-------|
| `advisory` | the rendered advisory body (empty when nothing was posted) |
| `classification` | `real` \| `likely_flaky` \| `unknown` (empty when no test was implicated) |
| `category` | `test_assertion` \| `compile_error` \| `dependency` \| `timeout` \| `infrastructure` \| `unknown` |
| `confidence` | diagnosis confidence, `0.00`–`1.00` (empty when nothing was posted) |

These let a workflow **route on the result** without parsing Markdown — e.g.
notify Slack only when `classification == 'real'`. See [§15](#15-slack-notifications).

**Permissions.** Minimal: `contents: read`, `pull-requests: write`,
`actions: read`.

**Fork PRs & secrets.** The `workflow_run` pattern runs Misfire in the *base*
repo's context, so secrets and a write token are available even when CI itself
ran from a fork — and a Misfire bug can't block the actual CI job. Misfire does
**not** gate on `workflow_run.pull_requests` (empty for forks); it resolves the PR
from the head commit instead.

---

## 10. Cost & safety controls

Misfire treats cost and noise as first-class constraints (this is where most
CI-LLM tools get abandoned):

- **Fires only on red builds** (the workflow `if` condition).
- **Log truncation.** Never dumps whole logs into context — keeps the failing
  tail plus the first error/traceback block from the dropped head.
- **Bounded agent loop.** A turn cap (default 12) and a token budget (default
  200,000 cumulative tokens). On hitting a limit before posting, Misfire stops
  gracefully with a clear message instead of looping. Override via `--max-turns`
  / `--token-budget` or the matching Action inputs.
- **Bounded history window.** Flaky history samples a small window of recent runs,
  with a per-run log cache within a single investigation.
- **Post at most once.** `post_pr_comment` is dispatched at most once per run, and
  upserts one comment per PR.
- **Never fails your CI.** An operational error (provider rate-limit/outage,
  GitHub API hiccup, expired logs) is caught, reported to the Action log, and
  exits cleanly — the Misfire step stays green and your build status is
  unchanged. Advisory means advisory, even when Misfire itself has a bad day.

---

## 11. Security

- **Secret scrubbing.** Logs and the diff are passed through a redaction pass
  (`misfire/scrub.py`) before reaching the model — provider API keys, GitHub/AWS/
  Slack tokens, JWTs, PEM private-key blocks, `Bearer`/`Authorization` values,
  secret-ish `key=value` pairs, and long high-entropy tokens become `[REDACTED]`.
  This is defense-in-depth behind GitHub's own log masking, not a guarantee.
- **Output sanitizing.** The model-authored `root_cause` / `suggested_fix` are
  sanitized before rendering: HTML-escaped, Markdown links/images neutralized,
  collapsed to a single line (so an attacker-controlled log can't inject new
  Markdown blocks or spoof the hidden upsert marker), and length-capped.
- **Prompt injection is bounded by design.** Log/diff content is
  attacker-influenceable, but the only write is a structured, rendered comment;
  the flaky verdict is deterministic; and Misfire never takes a code-mutating or
  merge action. The worst case is a misleading advisory, which sanitizing further
  constrains.
- **Token handling.** The GitHub token is never leaked across the redirect to the
  signed log-download URL (cross-origin `Authorization` is stripped).

---

## 12. Providers, models & endpoints

Misfire is **bring-your-own-key**: it never provides an API key, and every
analysis is billed to your account. Pick a provider with `--provider` (the
Action's `provider` input); the default is `claude`. You set the model yourself
(the `model` input / `--model`); each provider has a fallback default.

| Provider | `--provider` | Key env var | Default model | Install |
|----------|--------------|-------------|---------------|---------|
| Anthropic Claude | `claude` | `ANTHROPIC_API_KEY` | `claude-opus-4-8` | `pip install misfire-ci` |
| OpenAI | `openai` | `OPENAI_API_KEY` | `gpt-4o` | `pip install "misfire-ci[openai]"` |

```bash
# OpenAI instead of Claude:
misfire explain --run <id> --provider openai --model gpt-4o
```

**OpenAI-compatible endpoints (GitHub Copilot / GitHub Models).** If you have
access to GitHub's models, Misfire can use them through the OpenAI provider —
they expose an OpenAI-compatible API. Set `--provider openai`, point the OpenAI
SDK at the endpoint with `OPENAI_BASE_URL`, use your **GitHub token** as the key,
and choose that endpoint's model:

```bash
export OPENAI_BASE_URL="https://models.github.ai/inference"   # GitHub Models endpoint
export OPENAI_API_KEY="$GITHUB_TOKEN"                          # your GitHub token
misfire explain --run <id> --provider openai --model openai/gpt-4o
```

In the GitHub Action, the equivalent inputs are `provider: openai`,
`openai-base-url`, `openai-api-key` (your token), and `model`. Confirm the exact
endpoint URL and model ids against GitHub's current docs for your account.

Adding another provider is just a new adapter in `misfire/agents/` plus an entry
in `misfire/providers.py` — the tools, prompt, and flaky logic are shared.

---

## 13. How Misfire compares

| Tool | Open? | LLM | Multi-CI | Flaky handling | vs. Misfire |
|------|-------|-----|----------|----------------|-------------|
| **ci-why** | MIT | optional Claude | yes (log-agnostic) | none | Misfire adds flaky + diff/history fusion + PR integration; ci-why takes files/stdin and filters log noise |
| **ci-doctor** | MIT | optional (multi-provider) | GitHub/GitLab/Forgejo | none | closest cousin (deterministic-first, advisory, air-gap); Misfire adds real-vs-flaky + diff/history inputs |
| **Copilot "Explain error"** | commercial | yes | GitHub-only | none | built into github.com; Misfire is open, advisory-by-design, and does flaky classification |
| **Datadog / Trunk / Mergify** | commercial | partial/none | yes | full lifecycle (quarantine) | they detect flaky deterministically at scale; Misfire is free/OSS, advisory-only, and PR-native |

Misfire's distinctive combination among open-source tools: **logs + PR diff +
test history fused**, a **deterministic real-vs-flaky verdict**, **advisory-only**,
and **multi-provider, bring-your-own-key**.

---

## 14. Troubleshooting & FAQ

**Misfire didn't comment on a red build.** Check that: the watched workflow name
in `workflows: ["CI"]` matches your CI workflow exactly; the run actually
concluded as `failure`; and the run has an associated PR (pushes straight to the
default branch have none — Misfire no-ops). The resolution logs which case it hit.

**It commented, but on the wrong/old diagnosis.** Misfire upserts one comment per
PR — the latest red run's advisory replaces the previous one. If you expected a
new comment, that's by design.

**The flaky verdict says "unknown."** There wasn't enough history (the test wasn't
seen in the sampled window of recent runs), or you're running `misfire analyze`
offline where there is no history. Misfire never guesses "real" on missing data.

**It called a real failure "flaky" (or vice-versa).** The classifier leans flaky
on *any* failure on an unrelated commit (by design — never blame the diff for a
test that fails elsewhere), so one noisy unrelated failure can over-call flaky.
With a small history window it can also under-call. The verdict is a signal, not
a verdict of record.

**Cost is higher than expected.** Lower `--token-budget` / `--max-turns` (or the
Action inputs). Remember Misfire only fires on red builds and truncates logs
before they reach the model.

**Does it support GitLab / Jenkins / other CI?** The Action is GitHub-native. For
other systems, pipe logs into `misfire analyze --logs -` locally.

**Which model does it use?** Whatever you set (`--model` / the `model` input). It
defaults to `claude-opus-4-8` on Claude or `gpt-4o` on OpenAI; pick the backend
with `--provider`. See [§12](#12-providers-models--endpoints).

---

## 15. Slack notifications

Misfire posts to the PR, not to Slack — but forwarding is one extra step. The
Action exposes the rendered advisory as a step **output** (`advisory`), so you can
send it to a Slack [Incoming Webhook](https://api.slack.com/messaging/webhooks):

```yaml
jobs:
  explain:
    if: github.event.workflow_run.conclusion == 'failure'
    runs-on: ubuntu-latest
    steps:
      - id: misfire
        uses: ericmhayes/misfire-ci@v0
        with:
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
          github-token: ${{ secrets.GITHUB_TOKEN }}
          run-id: ${{ github.event.workflow_run.id }}
      - name: Notify Slack
        # Route on the structured output — e.g. only ping for a likely-real
        # failure, staying quiet on flaky noise. Drop the classification check to
        # forward every advisory.
        if: steps.misfire.outputs.advisory != '' && steps.misfire.outputs.classification == 'real'
        env:
          ADVISORY: ${{ steps.misfire.outputs.advisory }}
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
        run: |
          jq -nc --arg body "$ADVISORY" '{text: ("🔧 Misfire — build failure analysis:\n" + $body)}' \
            | curl -sS -X POST -H 'Content-type: application/json' --data @- "$SLACK_WEBHOOK_URL"
```

The Action also exposes `category` and `confidence` outputs (see
[§9](#9-github-action-reference)) for finer routing.

Create an Incoming Webhook in your Slack workspace and store its URL as the
`SLACK_WEBHOOK_URL` repository secret. Passing the advisory through an env var
(not string interpolation) keeps a hostile log from breaking out of the shell.
For the local path, pipe the CLI output yourself:
`misfire analyze --logs ci.log | your-slack-poster`.
