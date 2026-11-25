"""`misfire init` — scaffolding the workflow file.

Pure file generation, no network. Covers rendering, CI-name detection, and the
init command's write / detect / refuse-overwrite behavior.
"""

from __future__ import annotations

import os

from misfire import scaffold


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def test_render_claude_workflow() -> None:
    out = scaffold.render_workflow("CI", "claude")
    assert 'workflows: ["CI"]' in out
    assert "anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}" in out
    assert "ericmhayes/misfire-ci@v0" in out
    # GitHub expressions survive untouched (not mangled by a formatter).
    assert "${{ github.event.workflow_run.id }}" in out
    assert "provider: openai" not in out


def test_render_openai_workflow() -> None:
    out = scaffold.render_workflow("Build & Test", "openai")
    assert 'workflows: ["Build & Test"]' in out
    assert "provider: openai" in out
    assert "openai-api-key: ${{ secrets.OPENAI_API_KEY }}" in out


def test_detect_ci_workflow_skips_misfire(tmp_path) -> None:
    wf = tmp_path / ".github" / "workflows"
    _write(str(wf / "ci.yml"), "name: My CI\non: push\n")
    _write(str(wf / "misfire.yml"), "name: Misfire\non: workflow_run\n")
    assert scaffold.detect_ci_workflow(str(wf)) == "My CI"


def test_detect_returns_none_when_only_misfire(tmp_path) -> None:
    wf = tmp_path / ".github" / "workflows"
    _write(str(wf / "misfire.yml"), "name: Misfire\n")
    assert scaffold.detect_ci_workflow(str(wf)) is None


def test_init_writes_and_detects(tmp_path) -> None:
    _write(str(tmp_path / ".github" / "workflows" / "ci.yml"), 'name: "Unit Tests"\non: push\n')
    code, msg = scaffold.init(cwd=str(tmp_path))
    assert code == 0
    written = (tmp_path / ".github" / "workflows" / "misfire.yml").read_text()
    assert 'workflows: ["Unit Tests"]' in written
    assert "Unit Tests" in msg
    assert "ANTHROPIC_API_KEY" in msg


def test_init_defaults_ci_when_undetectable(tmp_path) -> None:
    code, msg = scaffold.init(cwd=str(tmp_path))  # no workflows dir at all
    assert code == 0
    written = (tmp_path / ".github" / "workflows" / "misfire.yml").read_text()
    assert 'workflows: ["CI"]' in written
    assert "defaulted to 'CI'" in msg


def test_init_refuses_to_overwrite(tmp_path) -> None:
    target = tmp_path / ".github" / "workflows" / "misfire.yml"
    _write(str(target), "existing\n")
    code, msg = scaffold.init(cwd=str(tmp_path))
    assert code == 1
    assert "already exists" in msg
    assert target.read_text() == "existing\n"  # untouched


def test_init_force_overwrites(tmp_path) -> None:
    target = tmp_path / ".github" / "workflows" / "misfire.yml"
    _write(str(target), "old\n")
    code, _ = scaffold.init(cwd=str(tmp_path), force=True, ci_workflow="CI")
    assert code == 0
    assert "misfire-ci@v0" in target.read_text()
