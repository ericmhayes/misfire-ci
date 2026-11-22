"""One place to turn the shared tool functions into provider schemas + dispatch.

Both the Claude adapter and the OpenAI adapter build their
tool lists from here, so the two adapters can't drift in which tools they
expose or how a tool call is dispatched back to the shared `tools.py` function.
"""

from __future__ import annotations

import enum
import inspect
import re
from collections.abc import Callable
from typing import Any

from .. import tools

# name -> the shared, @tool-decorated callable
DISPATCH: dict[str, Callable[..., str]] = {
    fn.__name__: fn for fn in tools.ALL_TOOLS
}

# Python annotation -> JSON-Schema primitive type.
_JSON_TYPES: dict[type, str] = {
    int: "integer",
    str: "string",
    float: "number",
    bool: "boolean",
}

_ARGS_HEADER = re.compile(r"^\s*(Args|Arguments):\s*$")
_SECTION_HEADER = re.compile(r"^\s*(Returns?|Raises|Yields|Examples?|Notes?):\s*$")
_ARG_ENTRY = re.compile(r"^(\s+)(\w+):\s*(.*)$")


def _parse_arg_docs(doc: str) -> dict[str, str]:
    """Map parameter name -> description from a Google-style ``Args:`` block.

    Without this only the first docstring paragraph reached the model, so the
    per-argument guidance — the valid `category` values, the confidence range,
    the `failing_test_id` format — was silently dropped and the model had to
    guess. Continuation lines (indented under an entry) are folded in.
    """
    lines = doc.splitlines()
    i = next((k for k, ln in enumerate(lines) if _ARGS_HEADER.match(ln)), None)
    if i is None:
        return {}

    out: dict[str, str] = {}
    cur: str | None = None
    entry_indent: int | None = None
    for ln in lines[i + 1 :]:
        if _SECTION_HEADER.match(ln):
            break
        if not ln.strip():
            continue
        m = _ARG_ENTRY.match(ln)
        indent = len(ln) - len(ln.lstrip())
        if m and (entry_indent is None or indent <= entry_indent):
            entry_indent = indent
            cur = m.group(2)
            out[cur] = m.group(3).strip()
        elif cur is not None:  # continuation of the current entry
            out[cur] = f"{out[cur]} {ln.strip()}".strip()
    return out


def _param_schema(annotation: Any) -> dict[str, Any]:
    """JSON-Schema fragment for one parameter's annotation (type, plus enum)."""
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        # e.g. FailureCategory -> {"type": "string", "enum": [...]}. This is how
        # the model learns the exact allowed `category` values instead of guessing.
        return {"type": "string", "enum": [e.value for e in annotation]}
    return {"type": _JSON_TYPES.get(annotation, "string")}


def _tool_schema(fn: Callable[..., str]) -> dict[str, Any]:
    """Build a {name, description, input_schema} entry from a tool function."""
    try:
        sig = inspect.signature(fn, eval_str=True)  # resolve stringized annotations
    except (TypeError, ValueError, NameError):
        sig = inspect.signature(fn)

    doc = (fn.__doc__ or "").strip()
    arg_docs = _parse_arg_docs(doc)

    properties: dict[str, Any] = {}
    required: list[str] = []
    for pname, p in sig.parameters.items():
        schema = _param_schema(p.annotation)
        if pname in arg_docs:
            schema["description"] = arg_docs[pname]
        properties[pname] = schema
        if p.default is inspect.Parameter.empty:
            required.append(pname)

    # Use the first paragraph of the docstring as the tool description.
    description = doc.split("\n\n", 1)[0].replace("\n", " ").strip()

    return {
        "name": fn.__name__,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def json_schemas() -> list[dict[str, Any]]:
    """Return JSON-Schema tool definitions derived from the shared tools.

    Built from each function's signature (parameters) and docstring (description).
    This is the neutral base; each adapter wraps it into its own tool format
    (Anthropic consumes it as-is; see `openai_schemas` for the OpenAI shape).
    """
    return [_tool_schema(fn) for fn in tools.ALL_TOOLS]


def openai_schemas() -> list[dict[str, Any]]:
    """Wrap the neutral schemas into OpenAI Chat Completions `tools` format.

    Same names/descriptions/parameters as `json_schemas`; only the envelope
    differs (`{"type": "function", "function": {...}}` with `parameters` instead
    of `input_schema`). Keeping this here — not in the adapter — means both the
    Claude and OpenAI adapters derive their tools from one source and can't drift.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": s["name"],
                "description": s["description"],
                "parameters": s["input_schema"],
            },
        }
        for s in json_schemas()
    ]


def dispatch(name: str, arguments: dict[str, Any]) -> str:
    """Invoke a shared tool by name. This is what fires the test harness's mock/trace."""
    if name not in DISPATCH:
        raise KeyError(f"Unknown tool: {name}")
    return DISPATCH[name](**arguments)
