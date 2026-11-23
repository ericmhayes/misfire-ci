"""Unit tests for the OpenAI adapter's loop — parity with the Claude adapter.

The write-safety contract (post_pr_comment at most once) and the return path
must hold identically across providers. These drive `openai.run` with a
scripted OpenAI client and a counting GitHub client.
"""

from __future__ import annotations

import os

from misfire.agents import openai as openai_adapter
from misfire.testkit import ScriptedOpenAI, oa_final, oa_tool_use


class CountingClient:
    """Records writes; get-methods are unused in these tests."""

    def __init__(self) -> None:
        self.posts: list[tuple[int, str]] = []

    def upsert_pr_comment(self, pr_number: int, body: str, *, marker: str) -> str:
        self.posts.append((pr_number, body))
        return f"https://github.com/x/y/pull/{pr_number}#issuecomment-1"


def _diagnosis(root_cause: str) -> dict:
    """Structured post_pr_comment args (no failing_test_id → no history lookup)."""
    return {"root_cause": root_cause, "category": "test_assertion", "confidence": 0.8}


def test_second_post_is_not_dispatched() -> None:
    client = CountingClient()
    script = [
        oa_tool_use("post_pr_comment", _diagnosis("first cause"), id="a"),
        oa_tool_use("post_pr_comment", _diagnosis("second cause"), id="b"),
        oa_final("done"),
    ]
    body = openai_adapter.run(1, 7, client, openai_client=ScriptedOpenAI(script))

    assert len(client.posts) == 1
    pr_number, posted = client.posts[0]
    assert pr_number == 7
    assert "first cause" in posted
    assert "second cause" not in posted
    assert body == posted


def test_single_post_dispatches_and_returns_body() -> None:
    client = CountingClient()
    script = [
        oa_tool_use("post_pr_comment", _diagnosis("only cause"), id="a"),
        oa_final("done"),
    ]
    body = openai_adapter.run(1, 7, client, openai_client=ScriptedOpenAI(script))

    assert len(client.posts) == 1
    assert client.posts[0][0] == 7
    assert "only cause" in body
    assert body == client.posts[0][1]


def test_no_post_returns_empty_string() -> None:
    client = CountingClient()
    script = [oa_final("nothing actionable")]
    body = openai_adapter.run(1, 7, client, openai_client=ScriptedOpenAI(script))

    assert client.posts == []
    assert body == ""


def test_empty_openai_base_url_is_dropped(monkeypatch) -> None:
    # The Action passes an unset openai-base-url through as "" — it must not
    # override the SDK default (that caused APIConnectionError in the wild).
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    openai_adapter._normalize_base_url_env()
    assert "OPENAI_BASE_URL" not in os.environ


def test_real_openai_base_url_is_preserved(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://models.github.ai/inference")
    openai_adapter._normalize_base_url_env()
    assert os.environ["OPENAI_BASE_URL"] == "https://models.github.ai/inference"
