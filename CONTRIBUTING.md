# Contributing to Misfire

Thanks for your interest! Misfire is an MIT-licensed, bring-your-own-key GitHub
Action + CLI that posts advisory CI-failure analysis. Contributions of all kinds
are welcome — bug reports, docs, and code.

## Ground rules

- **Advisory-only is a hard invariant.** Misfire's only write action is a single
  PR comment. Nothing may merge, block, quarantine, or change code. PRs that
  broaden the write surface won't be accepted.
- **Bring-your-own-key.** Misfire never ships or embeds an API key.
- Be kind — see the [Code of Conduct](CODE_OF_CONDUCT.md).

## Dev setup

Requires Python 3.10+.

```bash
git clone <your fork>
cd misfire-ci
pip install -e ".[dev]"      # add [openai] too if you work on the OpenAI provider
```

## Running the checks

```bash
ruff check .                 # lint (and `ruff check --fix .` to autofix)
python -m pytest -q          # unit tests
```

`ruff` + `pytest` are the full test gate and run in CI on every PR. The tests are
offline and deterministic — no API key, no network (the model is scripted and the
GitHub client is faked; see `misfire/testkit.py`).

## Sign your commits (DCO)

This project uses the [Developer Certificate of Origin](DCO) — a lightweight
way for you to certify that you wrote the contribution, or otherwise have the
right to submit it under the project's MIT license. There's nothing to sign:
just add a sign-off line to each commit with the `-s` flag.

```bash
git commit -s -m "your message"
```

That appends `Signed-off-by: Your Name <you@example.com>` using your git
identity. A CI check verifies every commit in a PR is signed off; if you forget:

```bash
git commit --amend -s          # fix the last commit
git rebase --signoff main      # fix all commits on your branch, then force-push
```

## Making a change

1. Branch off `main`.
2. Keep the change focused; match the surrounding style (ruff-clean, 100-col).
3. Add/adjust tests. New behavior needs a test.
4. Update docs (`README.md` / `docs/GUIDE.md`) when behavior changes.
5. Add a line under `## [Unreleased]` in [`CHANGELOG.md`](CHANGELOG.md).
6. Sign off your commits (`git commit -s`) — see above.
7. Open a PR; fill in the template. CI must be green.

## Adding an AI provider

Providers live behind one seam. To add one:

1. Write an adapter in `misfire/agents/<name>.py` exposing the same `run(...)`
   signature as `claude.py`.
2. Register it in `misfire/providers.py` (module path, key env var, default
   model, optional pip extra).
3. Keep provider-specific code in the adapter only — tools, prompt, and the flaky
   classifier are shared.

## Reporting bugs / security

- Bugs and features: open an issue (templates provided).
- **Security vulnerabilities: do not open a public issue** — see
  [SECURITY.md](SECURITY.md).
