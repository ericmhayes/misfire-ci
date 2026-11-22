"""The tool schemas handed to the model must carry per-argument docs and the
`category` enum — previously only the first docstring paragraph reached the
model, so it guessed category strings (then got coerced to `unknown`).
"""

from __future__ import annotations

from misfire.agents import _registry
from misfire.schema import FailureCategory


def _schema(name: str) -> dict:
    return next(s for s in _registry.json_schemas() if s["name"] == name)


def test_category_param_exposes_the_enum() -> None:
    props = _schema("post_pr_comment")["input_schema"]["properties"]
    assert props["category"]["type"] == "string"
    assert props["category"]["enum"] == [c.value for c in FailureCategory]
    # the human guidance from the docstring rides along too
    assert "Failure bucket" in props["category"]["description"]


def test_each_argument_gets_its_docstring_description() -> None:
    props = _schema("post_pr_comment")["input_schema"]["properties"]
    assert "root cause" in props["root_cause"]["description"].lower()
    assert "0.0-1.0" in props["confidence"]["description"]
    # continuation lines are folded into the entry
    assert "flaky verdict" in props["failing_test_id"]["description"].lower()


def test_required_excludes_optional_params() -> None:
    schema = _schema("post_pr_comment")["input_schema"]
    assert set(schema["required"]) == {"root_cause", "category", "confidence"}


def test_simple_tool_params_still_documented() -> None:
    props = _schema("get_failing_logs")["input_schema"]["properties"]
    assert props["run_id"]["type"] == "integer"
    assert "run id" in props["run_id"]["description"].lower()


def test_openai_schema_carries_the_enum_through() -> None:
    fns = [s["function"] for s in _registry.openai_schemas()]
    fn = next(f for f in fns if f["name"] == "post_pr_comment")
    assert fn["parameters"]["properties"]["category"]["enum"] == [c.value for c in FailureCategory]
