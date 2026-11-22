"""Shipping adapter: Claude.

Drives a tool-use loop against Claude using the shared tools/prompts. A manual
agentic loop is used (rather than the SDK tool runner) so a human-in-the-loop
gate can be placed in front of `post_pr_comment` if desired.
"""

from __future__ import annotations

import anthropic

from .. import prompts, tools
from ..github_client import GitHubClient
from ..github_history import GitHubTestHistoryProvider
from ..history import TestHistoryProvider
from . import _registry

MODEL = "claude-opus-4-8"

# Loop guardrails. The agent is normally done in a handful of turns; these
# cap a misbehaving/looping model so a single red build can't run unbounded API
# cost. Overridable from the Action (max-turns / token-budget inputs).
DEFAULT_MAX_TURNS = 12
DEFAULT_TOKEN_BUDGET = 200_000


def run(
    run_id: int,
    pr_number: int,
    client: GitHubClient,
    *,
    anthropic_client: "anthropic.Anthropic | None" = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    token_budget: int | None = DEFAULT_TOKEN_BUDGET,
    history_provider: "TestHistoryProvider | None" = None,
    model: str | None = None,
) -> str:
    """Investigate a failed run and post an advisory comment. Returns the body.

    Tests inject a scripted `anthropic_client` (and a fake GitHub client) so the
    run is fully offline — see `misfire.testkit`. In production `anthropic_client`
    is left None and a real client is created.

    The loop is bounded by `max_turns` (model round-trips) and `token_budget`
    (cumulative input+output tokens; None disables it). On hitting either limit
    before the agent posts, the run stops gracefully and returns a clear message
    instead of looping — if it already posted, the posted body is returned.
    """
    tools.bind(client)
    tools.bind_history(history_provider or GitHubTestHistoryProvider(client, run_id))
    tools.set_pr_number(pr_number)
    tools.set_run_id(run_id)

    anthropic_client = anthropic_client or anthropic.Anthropic()
    model = model or MODEL
    user = f"CI run {run_id} on PR #{pr_number} failed. Investigate and post the analysis."

    messages: list[dict] = [{"role": "user", "content": user}]
    tool_schemas = _registry.json_schemas()

    # The agent's single write action is `post_pr_comment`; the body it passes is
    # the advisory comment that lands on the PR. We capture it during the loop so
    # we can return it for logging/tests without a second model round-trip (the
    # scripted test client in testkit.py supplies a fixed number of turns).
    posted_body: str | None = None

    turns = 0
    tokens_used = 0

    # Manual agentic loop: keep going until Claude stops calling tools, bounded
    # by the turn cap and token budget (cost guardrail).
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

        response = anthropic_client.messages.create(
            model=model,
            max_tokens=4096,
            system=prompts.SYSTEM_PROMPT,
            tools=tool_schemas,
            messages=messages,
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            tokens_used += (getattr(usage, "input_tokens", 0) or 0) + (
                getattr(usage, "output_tokens", 0) or 0
            )
        if response.stop_reason != "tool_use":
            if response.stop_reason == "max_tokens" and posted_body is None:
                # The response was cut off at max_tokens mid-thought. Breaking here
                # would return "" and post nothing — a silent no-op. Surface it as
                # a diagnostic (cli routes this to stderr, not the PR).
                return (
                    "Misfire stopped: the model response hit the token limit before "
                    "it could post an advisory; no advisory was posted."
                )
            break

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type == "tool_use":
                if block.name == "post_pr_comment" and posted_body is not None:
                    # Advisory contract: post_pr_comment is the single write action
                    # and fires at most once. If the model asks to post again, we do
                    # NOT dispatch (no second GitHub comment) and tell it the advisory
                    # comment is already posted so it can wrap up.
                    output = (
                        "post_pr_comment was already called this run; the advisory "
                        "comment is posted at most once. Ignoring this duplicate call."
                    )
                else:
                    try:
                        # post_pr_comment renders and returns the advisory body;
                        # capture it (the tool, not the model, owns rendering).
                        output = _registry.dispatch(block.name, block.input)
                        if block.name == "post_pr_comment":
                            posted_body = output
                    except Exception as exc:
                        # Never crash the run on a bad tool call (unknown tool,
                        # wrong/missing args) — a hostile log can provoke these.
                        # Hand the error back so the model can correct and retry.
                        output = (
                            f"Tool '{block.name}' failed: {exc}. "
                            "Check the arguments and try again."
                        )
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": output}
                )
        messages.append({"role": "user", "content": results})

    # `post_pr_comment` renders and returns the advisory body, which we captured
    # above — return it verbatim for logging/tests. When the agent concludes
    # without posting (nothing actionable), return "".
    return posted_body or ""
