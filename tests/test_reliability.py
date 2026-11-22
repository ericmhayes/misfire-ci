"""Tier-0 reliability guarantees: Misfire is advisory-only and must never turn a
CI build red or silently post nothing because of an operational blip.

Covers four hardening fixes:
  1. The CLI `explain` live path catches any operational error and exits 0.
  2. The OpenAI adapter survives malformed tool-call argument JSON.
  3. `post_pr_comment` still posts (degrading to an UNKNOWN verdict) when the
     flaky-history lookup fails.
  4. A response truncated at the token limit surfaces a diagnostic instead of a
     silent no-op — in both adapters.
"""

from __future__ import annotations

import pytest

from misfire import cli, tools
from misfire.agents import claude as claude_adapter
from misfire.agents import openai as openai_adapter
from misfire.history import TestHistory
from misfire.testkit import (
    ScriptedAnthropic,
    ScriptedOpenAI,
    oa_final,
    oa_tool_use,
    oa_tool_use_raw,
    truncated,
)


class CountingClient:
    """Records advisory writes; nothing else is exercised here."""

    def __init__(self) -> None:
        self.posts: list[tuple[int, str]] = []

    def upsert_pr_comment(self, pr_number: int, body: str, *, marker: str) -> str:
        self.posts.append((pr_number, body))
        return f"https://github.com/x/y/pull/{pr_number}#issuecomment-1"


def _diagnosis(root_cause: str) -> dict:
    return {"root_cause": root_cause, "category": "test_assertion", "confidence": 0.8}


# ---------------------------------------------------------------------------
# 1. The CLI never crashes the Action red on an operational error.
# ---------------------------------------------------------------------------


@pytest.fixture
def _github_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")


class _ResolvingClient:
    def find_pr_number_for_run(self, run_id: int) -> int:
        return 42


def test_explain_exits_zero_when_the_run_raises(monkeypatch, capsys, _github_env) -> None:
    monkeypatch.setattr(cli, "_github_client", lambda: _ResolvingClient())

    def exploding_run(**_kw):
        raise RuntimeError("provider rate-limited (429)")

    monkeypatch.setattr(cli.providers, "get_run", lambda name: exploding_run)

    # Advisory-only: an operational failure must NOT fail the CI step.
    assert cli.main(["explain", "--run", "7"]) == 0
    out = capsys.readouterr()
    assert out.out == ""  # nothing on stdout → no downstream webhook fires
    assert "could not complete the investigation" in out.err
    assert "never fails your CI" in out.err
    assert "429" in out.err  # the real cause is surfaced for debugging


def test_explain_exits_zero_when_pr_resolution_raises(monkeypatch, capsys, _github_env) -> None:
    class _FailingClient:
        def find_pr_number_for_run(self, run_id: int):
            raise RuntimeError("GitHub API 503")

    monkeypatch.setattr(cli, "_github_client", lambda: _FailingClient())
    monkeypatch.setattr(cli.providers, "get_run", lambda name: (lambda **k: "unused"))

    assert cli.main(["explain", "--run", "7"]) == 0
    assert "GitHub API 503" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 2. The OpenAI adapter survives malformed tool-call argument JSON.
# ---------------------------------------------------------------------------


def test_openai_survives_malformed_tool_args() -> None:
    client = CountingClient()
    # First turn hands back invalid JSON arguments (previously an uncaught
    # JSONDecodeError that crashed the whole run), then the model recovers and
    # posts a valid advisory.
    script = [
        oa_tool_use_raw("post_pr_comment", "{not valid json", id="a"),
        oa_tool_use("post_pr_comment", _diagnosis("the real cause"), id="b"),
        oa_final("done"),
    ]
    body = openai_adapter.run(1, 7, client, openai_client=ScriptedOpenAI(script))

    assert len(client.posts) == 1  # the valid post still lands
    assert "the real cause" in body


def test_openai_malformed_args_alone_do_not_crash() -> None:
    client = CountingClient()
    script = [
        oa_tool_use_raw("post_pr_comment", "{broken", id="a"),
        oa_final("gave up cleanly"),
    ]
    # The run completes (no exception) and simply posts nothing.
    body = openai_adapter.run(1, 7, client, openai_client=ScriptedOpenAI(script))
    assert client.posts == []
    assert body == ""


# ---------------------------------------------------------------------------
# 3. A flaky-history failure degrades to UNKNOWN but still posts the advisory.
# ---------------------------------------------------------------------------


class _RaisingHistory:
    """History provider whose lookup always fails (expired logs / rate-limit)."""

    def get_history(self, test_id: str, *, current_sha=None) -> TestHistory:
        raise RuntimeError("run logs expired (410)")


def test_history_failure_still_posts_with_unknown_verdict() -> None:
    client = CountingClient()
    tools.bind(client)
    tools.set_pr_number(3)
    tools.bind_history(_RaisingHistory())

    body = tools.post_pr_comment(
        root_cause="The payment test blew up.",
        category="test_assertion",
        confidence=0.7,
        failing_test_id="tests/test_payments.py::test_charge",
    )

    assert len(client.posts) == 1  # advisory is NOT blocked by the history failure
    assert "Flakiness unknown" in body
    assert "unavailable" in body
    assert client.posts[0][1] == body


# ---------------------------------------------------------------------------
# 4. A token-limit truncation is a diagnostic, not a silent no-op.
# ---------------------------------------------------------------------------


def test_claude_truncation_surfaces_diagnostic() -> None:
    client = CountingClient()
    # The very first response is cut off at max_tokens with no tool call.
    body = claude_adapter.run(
        1, 7, client, anthropic_client=ScriptedAnthropic([truncated("cut off mid-")])
    )
    assert client.posts == []
    assert "token limit" in body
    assert "no advisory was posted" in body


def test_openai_truncation_surfaces_diagnostic() -> None:
    client = CountingClient()
    body = openai_adapter.run(
        1, 7, client, openai_client=ScriptedOpenAI([oa_final("", finish_reason="length")])
    )
    assert client.posts == []
    assert "token limit" in body


def test_truncation_after_posting_returns_the_advisory() -> None:
    # If the model already posted, a later truncated turn must still return the
    # posted advisory, not the diagnostic.
    client = CountingClient()
    script = [
        oa_tool_use("post_pr_comment", _diagnosis("already posted"), id="a"),
        oa_final("", finish_reason="length"),
    ]
    body = openai_adapter.run(1, 7, client, openai_client=ScriptedOpenAI(script))
    assert len(client.posts) == 1
    assert "already posted" in body
