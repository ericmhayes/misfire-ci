"""OpenAI provider adapter.

Exposes the same `run(...)` entrypoint as `claude.py` and drives the shared tools
and prompt (`tools.py`, `prompts.py`, `flaky.py`) through OpenAI's Chat
Completions tool-call loop. Selected at runtime with `--provider openai` (the
`openai` package is an optional dependency, installed with `misfire-ci[openai]`).

Kept deliberately thin: only the provider-specific loop lives here. Everything
else is shared, so adding a provider is just another adapter like this one.
"""

from __future__ import annotations

import json

import openai

from .. import prompts, tools
from ..github_client import GitHubClient
from ..github_history import GitHubTestHistoryProvider
from ..history import TestHistoryProvider
from . import _registry

# Fallback model; the user sets their own via the Action's `model` input /
# `misfire --model`. The endpoint is whatever the openai SDK points at — it
# honors OPENAI_BASE_URL, so this adapter also drives any OpenAI-compatible
# endpoint (e.g. GitHub Models / Copilot) by setting OPENAI_BASE_URL +
# OPENAI_API_KEY (your GitHub token) and choosing that endpoint's model.
MODEL = "gpt-4o"

# Loop guardrails — kept in step with the Claude adapter so provider choice does
# not change the cost ceiling.
DEFAULT_MAX_TURNS = 12
DEFAULT_TOKEN_BUDGET = 200_000


def run(
    run_id: int,
    pr_number: int,
    client: GitHubClient,
    *,
    openai_client: "openai.OpenAI | None" = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    token_budget: int | None = DEFAULT_TOKEN_BUDGET,
    history_provider: "TestHistoryProvider | None" = None,
    model: str | None = None,
) -> str:
    """Investigate a failed run and post an advisory comment. Returns the body.

    Mirrors `claude.run` — same tools, prompt, single-write contract, and
    turn/token guardrails — over OpenAI's tool-call loop. Tests inject a scripted
    `openai_client` so the run is fully offline.
    """
    tools.bind(client)
    tools.bind_history(history_provider or GitHubTestHistoryProvider(client, run_id))
    tools.set_pr_number(pr_number)
    tools.set_run_id(run_id)

    openai_client = openai_client or openai.OpenAI()
    model = model or MODEL
    user = f"CI run {run_id} on PR #{pr_number} failed. Investigate and post the analysis."

    messages: list = [
        {"role": "system", "content": prompts.SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    tool_schemas = _registry.openai_schemas()

    # Same single-write contract as the Claude adapter: capture the posted body
    # and refuse any second post_pr_comment (see claude.py for the rationale).
    posted_body: str | None = None

    turns = 0
    tokens_used = 0

    while True:
        if turns >= max_turns:
            return posted_body or (
                f"Misfire stopped: reached the {max_turns}-turn investigation limit "
                "before reaching a conclusion; no advisory was posted."
            )
        if token_budget is not None and tokens_used >= token_budget:
            return posted_body or (
                f"Misfire stopped: reached the {token_budget}-token budget "
                "before reaching a conclusion; no advisory was posted."
            )
        turns += 1

        response = openai_client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tool_schemas,
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            tokens_used += (getattr(usage, "prompt_tokens", 0) or 0) + (
                getattr(usage, "completion_tokens", 0) or 0
            )
        message = response.choices[0].message
        if not message.tool_calls:
            if getattr(response.choices[0], "finish_reason", None) == "length" and (
                posted_body is None
            ):
                # Response truncated at the token limit with nothing posted —
                # mirror the Claude adapter and surface a diagnostic rather than
                # returning "" (a silent no-op).
                return (
                    "Misfire stopped: the model response hit the token limit before "
                    "it could post an advisory; no advisory was posted."
                )
            break

        messages.append(message)
        for call in message.tool_calls:
            name = call.function.name
            if name == "post_pr_comment" and posted_body is not None:
                output = (
                    "post_pr_comment was already called this run; the advisory "
                    "comment is posted at most once. Ignoring this duplicate call."
                )
            else:
                try:
                    # Parse the arguments INSIDE the try: OpenAI delivers tool-call
                    # arguments as a raw JSON string, and a malformed one must be
                    # handled like any other bad tool call — not crash the run.
                    arguments = json.loads(call.function.arguments or "{}")
                    # post_pr_comment renders and returns the advisory body; capture it.
                    output = _registry.dispatch(name, arguments)
                    if name == "post_pr_comment":
                        posted_body = output
                except Exception as exc:
                    # Never crash the run on a bad tool call — unknown tool,
                    # malformed JSON arguments, or wrong/missing args (a hostile
                    # log can provoke these). Hand the error back so the model can
                    # correct and retry.
                    output = (
                        f"Tool '{name}' failed: {exc}. Check the arguments and try again."
                    )
            messages.append({"role": "tool", "tool_call_id": call.id, "content": output})

    return posted_body or ""
