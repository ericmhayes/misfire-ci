"""Scripted model clients for offline, deterministic agent tests.

The agent loops drive a live ``messages.create`` / ``chat.completions.create``
call. To test them with no API key and no network, these drop-in clients replay
a **fixed sequence of turns** (tool calls then a terminal turn). Paired with a
fake GitHub client in the test, a whole run is fully offline and deterministic.

Use :class:`ScriptedAnthropic` / :class:`ScriptedOpenAI` and the ``tool_use`` /
``final`` (and ``oa_*``) turn builders — see ``tests/test_agent_loop.py``.
"""

from __future__ import annotations

import json
from typing import Any

# ---------------------------------------------------------------------------
# Scripted Claude — replays a fixed sequence of turns for the tool-use loop.
# Shapes match what agents.claude.run reads: response.stop_reason and
# response.content[] blocks with .type / .name / .input / .id.
# ---------------------------------------------------------------------------

class _Block:
    def __init__(self, type: str, *, name: str | None = None,
                 input: dict | None = None, id: str | None = None,
                 text: str | None = None) -> None:
        self.type = type
        self.name = name
        self.input = input or {}
        self.id = id
        self.text = text


class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Response:
    def __init__(
        self, stop_reason: str, content: list[_Block], usage: "_Usage | None" = None
    ) -> None:
        self.stop_reason = stop_reason
        self.content = content
        if usage is not None:
            self.usage = usage


class _Messages:
    def __init__(self, script: list[_Response]) -> None:
        self._script = list(script)
        self._i = 0
        self.calls = 0

    def create(self, **_kwargs: Any) -> _Response:
        self.calls += 1
        if self._i >= len(self._script):
            raise AssertionError(
                "ScriptedAnthropic ran out of scripted turns — the agent looped "
                "more times than the script provided."
            )
        resp = self._script[self._i]
        self._i += 1
        return resp


class ScriptedAnthropic:
    """A drop-in stand-in for ``anthropic.Anthropic()`` that replays turns."""

    def __init__(self, script: list[_Response]) -> None:
        self.messages = _Messages(script)


def tool_use(
    name: str, tool_input: dict, *, id: str = "call", tokens: int = 0
) -> _Response:
    """A turn where Claude requests one tool call.

    `tokens` sets a per-turn usage (input tokens) so tests can exercise the
    token budget; it defaults to 0 (usage absent), matching the earlier shape.
    """
    usage = _Usage(tokens, 0) if tokens else None
    return _Response(
        "tool_use", [_Block("tool_use", name=name, input=tool_input, id=id)], usage
    )


def final(text: str = "", *, tokens: int = 0) -> _Response:
    """The terminal turn (Claude stops calling tools)."""
    usage = _Usage(tokens, 0) if tokens else None
    return _Response("end_turn", [_Block("text", text=text)], usage)


def truncated(text: str = "", *, tokens: int = 0) -> _Response:
    """A turn cut off at the token limit (stop_reason ``max_tokens``)."""
    usage = _Usage(tokens, 0) if tokens else None
    return _Response("max_tokens", [_Block("text", text=text)], usage)


# ---------------------------------------------------------------------------
# Scripted OpenAI — the same idea for the OpenAI adapter. Shapes match what
# agents.openai.run reads: response.choices[0].message with .tool_calls (each
# with .id and .function.name/.arguments) and .content.
# ---------------------------------------------------------------------------

class _OAFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _OAToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
        self.type = "function"
        self.function = _OAFunction(name, arguments)


class _OAMessage:
    def __init__(self, *, content: str | None = None,
                 tool_calls: list[_OAToolCall] | None = None) -> None:
        self.role = "assistant"
        self.content = content
        self.tool_calls = tool_calls


class _OAResponse:
    def __init__(self, message: _OAMessage, *, finish_reason: str | None = None) -> None:
        self.choices = [
            type("_Choice", (), {"message": message, "finish_reason": finish_reason})()
        ]


class _OACompletions:
    def __init__(self, script: list[_OAResponse]) -> None:
        self._script = list(script)
        self._i = 0
        self.calls = 0

    def create(self, **_kwargs: Any) -> _OAResponse:
        self.calls += 1
        if self._i >= len(self._script):
            raise AssertionError(
                "ScriptedOpenAI ran out of scripted turns — the agent looped "
                "more times than the script provided."
            )
        resp = self._script[self._i]
        self._i += 1
        return resp


class ScriptedOpenAI:
    """A drop-in stand-in for ``openai.OpenAI()`` that replays turns."""

    def __init__(self, script: list[_OAResponse]) -> None:
        self.chat = type("_Chat", (), {"completions": _OACompletions(script)})()


def oa_tool_use(name: str, tool_input: dict, *, id: str = "call") -> _OAResponse:
    """A turn where the OpenAI model requests one tool call."""
    return _OAResponse(
        _OAMessage(tool_calls=[_OAToolCall(id, name, json.dumps(tool_input))])
    )


def oa_tool_use_raw(name: str, arguments: str, *, id: str = "call") -> _OAResponse:
    """A tool-call turn with raw (possibly malformed) argument text.

    OpenAI delivers tool-call arguments as a JSON string the adapter must parse;
    this lets a test inject invalid JSON to exercise that failure path.
    """
    return _OAResponse(_OAMessage(tool_calls=[_OAToolCall(id, name, arguments)]))


def oa_final(text: str = "", *, finish_reason: str | None = None) -> _OAResponse:
    """The terminal turn (model stops calling tools).

    Pass ``finish_reason="length"`` to simulate a response truncated at the token
    limit.
    """
    return _OAResponse(
        _OAMessage(content=text, tool_calls=None), finish_reason=finish_reason
    )
