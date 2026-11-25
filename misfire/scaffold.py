"""`misfire init` — scaffold a Misfire workflow into a repo.

Collapses the quickstart (copy YAML, figure out your CI workflow's name, paste)
into one command. Detects the CI workflow to watch, writes
`.github/workflows/misfire.yml`, and prints the one remaining step (add your key).
No GitHub, no network — pure file generation.
"""

from __future__ import annotations

import glob
import os
import re

DEFAULT_PATH = os.path.join(".github", "workflows", "misfire.yml")

# Provider-specific `with:` inputs (10-space indented; end with a newline so the
# shared inputs follow cleanly).
_PROVIDER_INPUTS = {
    "claude": "          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}\n",
    "openai": (
        "          provider: openai\n"
        "          openai-api-key: ${{ secrets.OPENAI_API_KEY }}\n"
    ),
}

_KEY_SECRET = {"claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}

# Sentinel placeholders (not str.format) so the literal `${{ ... }}` GitHub
# expressions survive untouched.
_TEMPLATE = """\
name: Misfire

# Runs after your CI completes (decoupled workflow_run trigger), so Misfire gets a
# write-scoped token (works on fork PRs) and can never block or slow your CI.
on:
  workflow_run:
    workflows: ["__CI_WORKFLOW__"]
    types: [completed]

permissions:
  contents: read
  pull-requests: write
  actions: read

jobs:
  explain:
    # Only fire on red builds.
    if: github.event.workflow_run.conclusion == 'failure'
    runs-on: ubuntu-latest
    steps:
      - uses: ericmhayes/misfire-ci@v0
        with:
__PROVIDER_INPUTS__          github-token: ${{ secrets.GITHUB_TOKEN }}
          run-id: ${{ github.event.workflow_run.id }}
"""

_NAME_RE = re.compile(r"""^name:\s*["']?(.+?)["']?\s*$""")


def _read_workflow_name(path: str) -> str | None:
    """The top-level `name:` of a workflow file (no YAML dependency)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith((" ", "\t", "#")):
                    continue  # top-level key only
                m = _NAME_RE.match(line)
                if m:
                    return m.group(1)
    except OSError:
        return None
    return None


def detect_ci_workflow(workflows_dir: str) -> str | None:
    """Best-effort: the `name:` of the first workflow that isn't Misfire's own."""
    paths = sorted(glob.glob(os.path.join(workflows_dir, "*.yml")))
    paths += sorted(glob.glob(os.path.join(workflows_dir, "*.yaml")))
    for path in paths:
        if "misfire" in os.path.basename(path).lower():
            continue
        name = _read_workflow_name(path)
        if name and name.lower() != "misfire":
            return name
    return None


def render_workflow(ci_workflow: str, provider: str = "claude") -> str:
    """Render the Misfire workflow YAML for a given CI workflow name + provider."""
    inputs = _PROVIDER_INPUTS.get(provider, _PROVIDER_INPUTS["claude"])
    return _TEMPLATE.replace("__CI_WORKFLOW__", ci_workflow).replace(
        "__PROVIDER_INPUTS__", inputs
    )


def init(
    *,
    ci_workflow: str | None = None,
    provider: str = "claude",
    path: str | None = None,
    force: bool = False,
    cwd: str = ".",
) -> tuple[int, str]:
    """Write the Misfire workflow. Returns (exit_code, message)."""
    target = path or DEFAULT_PATH
    abs_target = os.path.join(cwd, target)
    if os.path.exists(abs_target) and not force:
        return 1, f"{target} already exists. Re-run with --force to overwrite it."

    detected = ci_workflow or detect_ci_workflow(
        os.path.join(cwd, ".github", "workflows")
    )
    ci = detected or "CI"
    body = render_workflow(ci, provider)

    os.makedirs(os.path.dirname(abs_target), exist_ok=True)
    with open(abs_target, "w", encoding="utf-8") as fh:
        fh.write(body)

    lines = [f"Wrote {target} — it watches your '{ci}' workflow and comments on red builds."]
    if not ci_workflow and not detected:
        lines.append(
            "Couldn't detect your CI workflow name, so defaulted to 'CI'. If that's "
            "not it, edit `workflows: [...]` (or re-run with --ci-workflow <name>)."
        )
    lines.append(
        f"Next: add your {_KEY_SECRET.get(provider, 'ANTHROPIC_API_KEY')} as a "
        "repository secret (Settings → Secrets and variables → Actions), then commit "
        "the file. Misfire is bring-your-own-key."
    )
    return 0, "\n".join(lines)
