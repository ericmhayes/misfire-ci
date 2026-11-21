# Changelog — Misfire

All notable changes are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/); this project uses
[Semantic Versioning](https://semver.org/) (pre-1.0: minor may include breaking
changes). Dates are absolute (YYYY-MM-DD).

## [Unreleased]

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
