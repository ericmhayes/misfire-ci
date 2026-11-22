"""CLI wiring: provider selection, bring-your-own-key guard, PR resolution.

These patch the GitHub client and the provider dispatch so no network, no model,
and no real key are involved.
"""

from __future__ import annotations

import json

import pytest

from misfire import cli
from misfire.schema import FailureCategory, FlakyAssessment, FlakyClassification, Verdict


@pytest.fixture(autouse=True)
def _github_env(monkeypatch):
    # `explain` requires GitHub credentials (set automatically inside the Action).
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")


class _FakeClient:
    def __init__(self, resolved: int | None) -> None:
        self._resolved = resolved
        self.resolved_for: int | None = None

    def find_pr_number_for_run(self, run_id: int) -> int | None:
        self.resolved_for = run_id
        return self._resolved


def _recording_run(record: dict):
    def fake_run(run_id, pr_number, client, *, model=None, **kw):
        record["args"] = (run_id, pr_number)
        record["model"] = model
        return "BODY"

    return fake_run


def test_explain_resolves_pr_and_dispatches(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    client = _FakeClient(resolved=42)
    rec: dict = {}
    monkeypatch.setattr(cli, "_github_client", lambda: client)
    monkeypatch.setattr(cli.providers, "get_run", lambda name: _recording_run(rec))

    assert cli.main(["explain", "--run", "7"]) == 0
    assert client.resolved_for == 7
    assert rec["args"] == (7, 42)
    assert rec["model"] == "claude-opus-4-8"  # provider default when --model omitted


def test_explain_noops_when_no_pr(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    ran: list = []
    monkeypatch.setattr(cli, "_github_client", lambda: _FakeClient(resolved=None))
    monkeypatch.setattr(cli.providers, "get_run", lambda name: (lambda **k: ran.append(k)))

    assert cli.main(["explain", "--run", "7"]) == 0
    assert ran == []  # no PR → agent never runs


def test_explain_requires_key_byo(monkeypatch, capsys) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    touched: list = []
    monkeypatch.setattr(cli, "_github_client", lambda: touched.append("gh"))

    assert cli.main(["explain", "--run", "7"]) == 2
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY is not set" in err
    assert "bring-your-own-key" in err
    assert touched == []  # bails before doing anything


def test_explain_openai_provider(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "y")
    rec: dict = {}
    picked: dict = {}
    monkeypatch.setattr(cli, "_github_client", lambda: _FakeClient(resolved=5))

    def fake_get_run(name):
        picked["name"] = name
        return _recording_run(rec)

    monkeypatch.setattr(cli.providers, "get_run", fake_get_run)

    rc = cli.main(["explain", "--run", "3", "--provider", "openai", "--model", "gpt-4o-mini"])
    assert rc == 0
    assert picked["name"] == "openai"
    assert rec["args"] == (3, 5)
    assert rec["model"] == "gpt-4o-mini"  # user-set model threaded through


def test_explain_openai_missing_key(monkeypatch, capsys) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert cli.main(["explain", "--run", "3", "--provider", "openai"]) == 2
    assert "OPENAI_API_KEY is not set" in capsys.readouterr().err


def test_analyze_no_ai_needs_no_key(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    log = tmp_path / "ci.log"
    log.write_text("ModuleNotFoundError: No module named 'requests'\n")
    assert cli.main(["analyze", "--logs", str(log), "--no-ai"]) == 0
    assert "`dependency`" in capsys.readouterr().out


def test_analyze_no_ai_json(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    log = tmp_path / "ci.log"
    log.write_text("ModuleNotFoundError: No module named 'requests'\n")
    assert cli.main(["analyze", "--logs", str(log), "--no-ai", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "root_cause": None,
        "category": "dependency",
        "confidence": None,
        "classification": None,
        "suggested_fix": None,
    }


def test_analyze_ai_json_from_verdict(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    log = tmp_path / "ci.log"
    log.write_text("E AssertionError\n")
    verdict = Verdict(
        root_cause="the change broke the assertion",
        category=FailureCategory.TEST_ASSERTION,
        confidence=0.9,
        suggested_fix="revert it",
        flaky=FlakyAssessment(
            classification=FlakyClassification.REAL, confidence=0.8, rationale="r"
        ),
    )
    monkeypatch.setattr(cli.local, "analyze_with_ai", lambda *a, **k: "BODY")
    monkeypatch.setattr(cli.tools, "last_verdict", lambda: verdict)

    assert cli.main(["analyze", "--logs", str(log), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["category"] == "test_assertion"
    assert data["classification"] == "real"
    assert data["confidence"] == 0.9
    assert data["root_cause"] == "the change broke the assertion"


def test_emit_action_outputs_writes_github_output(monkeypatch, tmp_path) -> None:
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    verdict = Verdict(
        root_cause="x",
        category=FailureCategory.TIMEOUT,
        confidence=0.73,
        flaky=FlakyAssessment(
            classification=FlakyClassification.LIKELY_FLAKY, confidence=0.6, rationale="r"
        ),
    )
    cli._emit_action_outputs(verdict)
    written = out.read_text()
    assert "classification=likely_flaky" in written
    assert "category=timeout" in written
    assert "confidence=0.73" in written


def test_emit_action_outputs_noop_without_env(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    cli._emit_action_outputs(None)  # must not raise
