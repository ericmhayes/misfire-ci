# Changelog — Misfire

All notable changes are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/); this project uses
[Semantic Versioning](https://semver.org/) (pre-1.0: minor may include breaking
changes). Dates are absolute (YYYY-MM-DD).

## [Unreleased]

### Fixed
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
