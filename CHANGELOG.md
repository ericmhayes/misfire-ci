# Changelog — Misfire

All notable changes are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/); this project uses
[Semantic Versioning](https://semver.org/) (pre-1.0: minor may include breaking
changes). Dates are absolute (YYYY-MM-DD).

## [Unreleased]

## [0.1.5] — 2026-11-23

### Changed
- **The Action pins the package to its ref** — when used via a version tag
  (`ericmhayes/misfire-ci@v0.1.5`), the Action now installs the matching
  `misfire-ci==0.1.5` instead of whatever is newest on PyPI, so a pinned ref gives
  reproducible behavior. A floating ref (`@v0`) or a branch still tracks the
  latest. (Previously every ref installed the newest release.)

## [0.1.4] — 2026-11-23

### Changed
- **Advisory header shows the Misfire logo** — the comment title now leads with
  the brand icon (an absolute PNG GitHub proxies; reads on light and dark themes)
  instead of a wrench emoji.

## [0.1.3] — 2026-11-23

### Fixed
- **OpenAI provider connection failure when no base URL is set** — the Action
  passes an unset `openai-base-url` through as an empty string, and the OpenAI SDK
  treated that empty `OPENAI_BASE_URL` as a base-URL override rather than falling
  back to `api.openai.com`, raising `APIConnectionError`. The adapter now drops an
  empty `OPENAI_BASE_URL`, and the Action unsets it when blank. (Found by
  dogfooding Misfire on its own demo repo.)

## [0.1.2] — 2026-11-22

### Added
- **Structured output** — `misfire analyze --format json` emits the verdict as
  JSON (root cause, category, confidence, real-vs-flaky classification), and the
  Action now exposes `classification`, `category`, and `confidence` outputs
  alongside `advisory`. Route notifications on the result (e.g. Slack only when
  `classification == real`) or feed other systems without parsing Markdown.
- **Evidence block in the advisory** — every comment now carries a collapsed,
  Misfire-authored "Evidence" section: a link to the failing run and the raw
  history counts the flaky verdict was computed from (runs sampled, failures,
  failures on other branches, same-commit retries). Nothing in it is
  model-authored, so a reader can verify the call instead of trusting it.
- **Pass-on-retry flaky signal** — the classifier now recognizes the strongest
  flaky evidence GitHub offers: a test that failed on one attempt and passed on a
  re-run of the *same commit*. Since the code didn't change between attempts, a
  red→green flip is near-proof the failure is non-deterministic, and it outranks
  the cross-commit signal.

### Changed
- **Diagnoses read the failing job, not the whole run** — `fetch_failing_logs`
  now pulls only the jobs that actually failed (per-job logs), so on a matrix
  build the model sees the broken leg instead of a passing job's tail. Falls back
  to the whole-run archive when the jobs API has nothing usable.
- **Real-vs-flaky detection spans more test runners** — the failure-marker set
  now recognizes Go (`--- FAIL`), Jest/Mocha/Vitest (`✕`), and TAP (`not ok`) in
  addition to pytest, so a flaky non-Python test is no longer mislabeled as a
  real regression.
- **The model now sees full tool-argument docs** — per-argument descriptions and
  the `category` enum are included in the tool schema (previously only the first
  docstring line reached the model, so it guessed the category).

### Fixed
- **Oversized logs are bounded by bytes, not just lines** — individual lines are
  clamped and the whole log slice is capped, so one pathological line (a huge
  minified bundle / single-line JSON) can't flood the model context.
- **Never fail the CI step** — the `explain` path now catches any operational
  error (provider rate-limit/outage, GitHub API error, expired logs, a missing
  provider SDK), reports the cause to stderr, and exits 0. Misfire is
  advisory-only; an internal blip must never turn a build red.
- **OpenAI adapter: malformed tool-call arguments no longer crash the run** — the
  argument JSON is now parsed inside the error-handling block and fed back to the
  model like any other bad tool call.
- **A flaky-history lookup failure no longer blocks the advisory** — if test
  history can't be fetched (e.g. expired run logs), `post_pr_comment` degrades to
  an explicit "flakiness unknown" verdict and still posts the diagnosis.
- **Token-limit truncation surfaces a diagnostic instead of a silent no-op** —
  both adapters now report when a response is cut off at the token limit before
  an advisory is posted.

## [0.1.1] — 2026-11-21

### Fixed
- **PyPI README rendering** — the project page's header logo was broken because
  it used a relative path to an SVG; PyPI renders the README standalone (relative
  paths 404) and strips SVG images. Now an absolute PNG URL.
- **License section** — reduced to the license itself (MIT); the
  bring-your-own-key note it carried (and its outdated "Anthropic API key"
  wording) is already covered in the intro.

## [0.1.0] — 2026-11-21

### Added
- **Advisory CI-failure analysis** — on a red build, an agentic investigation
  over the failing logs, the PR diff, and test history, posted as a single
  advisory PR comment (root cause, category, confidence). Never blocks, merges,
  or changes code.
- **Real-vs-flaky classification** — a deterministic statistics layer over run
  history labels a failing test real / likely-flaky / unknown (the model can read
  it but can't fabricate it).
- **Multiple providers, bring-your-own-key** — run on Claude or OpenAI via
  `--provider` (the Action `provider` input); set the model with `--model`. The
  OpenAI provider also drives OpenAI-compatible endpoints (e.g. GitHub Models /
  Copilot) via `OPENAI_BASE_URL`. Misfire never provides an API key.
- **Fork-PR safe** — resolves the PR from the failed run's head commit, so the
  `workflow_run`-based Action works on fork PRs.
- **One rolling comment per PR** — advisories upsert in place via a hidden marker
  instead of piling up.
- **Local / offline mode** — `misfire analyze --logs <file|->`, with a `--no-ai`
  deterministic summary that needs no key.
- **Cost & safety controls** — log truncation, a turn cap and token budget, secret
  scrubbing of logs/diff, and output sanitizing of the rendered comment.
- **Slack-ready** — the Action exposes an `advisory` output for a follow-on
  webhook step.

## [0.0.0] — 2026-08-18
- Initial scaffold: agentic CI-failure explainer.
