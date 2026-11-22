"""Entrypoint. Two subcommands:

    misfire explain --run <run_id> [--pr <n>] [--provider <p>]   # GitHub: fetch + comment
    misfire analyze --logs <file|-> [--no-ai] [--provider <p>]   # local: analyze, print

Bring-your-own-key: Misfire never provides an API key. Pick a provider with
`--provider` (default claude) and set that provider's key env var; `--no-ai`
needs no key.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import local, providers, tools
from .comment import MARKER
from .github_client import GitHubClient


def _verdict_to_json(verdict) -> dict:
    """Machine-readable view of a Verdict (stable key set)."""
    return {
        "root_cause": verdict.root_cause,
        "category": verdict.category.value,
        "confidence": round(verdict.confidence, 2),
        "classification": verdict.flaky.classification.value if verdict.flaky else None,
        "suggested_fix": verdict.suggested_fix,
    }


def _logs_only_json(logs: str) -> dict:
    """Same key set, for when there's no model verdict (--no-ai, or no advisory)."""
    return {
        "root_cause": None,
        "category": local.categorize(logs).value,
        "confidence": None,
        "classification": None,
        "suggested_fix": None,
    }


def _emit_action_outputs(verdict) -> None:
    """Write classification/category/confidence to $GITHUB_OUTPUT when present.

    Lets a workflow route on the result (e.g. notify only when classification is
    `real`) without parsing the advisory markdown. No-op outside Actions.
    """
    path = os.environ.get("GITHUB_OUTPUT")
    if not path or verdict is None:
        return
    data = _verdict_to_json(verdict)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"classification={data['classification'] or ''}\n")
        fh.write(f"category={data['category']}\n")
        fh.write(f"confidence={data['confidence']}\n")


def _github_client() -> GitHubClient:
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]  # "owner/name", set by Actions
    return GitHubClient(token=token, repo=repo)


def _read_text(path: str) -> str:
    """Read a file, or stdin when path is '-'."""
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _require_key(provider_name: str) -> bool:
    """True if the chosen provider's API key is set; else print a BYO-key message.

    Misfire is bring-your-own-key — it never supplies a key, and every analysis
    is billed to the user's own account.
    """
    provider = providers.get(provider_name)
    if os.environ.get(provider.key_env):
        return True
    extra = (
        f" (install the SDK with `pip install \"misfire-ci[{provider.extra}]\"`)"
        if provider.extra
        else ""
    )
    print(
        f"{provider.key_env} is not set. Misfire is bring-your-own-key — it does NOT "
        f"provide an API key for {provider.label}. Set your own (export "
        f"{provider.key_env}=...); every analysis calls the provider's API and is "
        f"billed to your account.{extra}\n"
        "No key? Use `misfire analyze --no-ai` for a deterministic summary with no "
        "model call.",
        file=sys.stderr,
    )
    return False


def _add_provider_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--provider",
        choices=providers.names(),
        default=providers.DEFAULT_PROVIDER,
        help="AI provider to run on (bring your own key). Default: "
        f"{providers.DEFAULT_PROVIDER}.",
    )
    p.add_argument(
        "--model",
        default=None,
        help="Model to use. You set this; falls back to the provider's default. "
        "For an OpenAI-compatible endpoint (e.g. GitHub Models / Copilot) set "
        "--provider openai, OPENAI_BASE_URL, and the endpoint's model here.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="misfire", description="Explain a red CI build.")
    sub = parser.add_subparsers(dest="command", required=True)

    explain = sub.add_parser("explain", help="Investigate a failed run and comment on its PR.")
    explain.add_argument("--run", type=int, required=True, help="Failed workflow run ID.")
    explain.add_argument(
        "--pr",
        type=int,
        default=None,
        help="Pull request number. Resolved from the run's head commit when omitted "
        "(so fork PRs work, where workflow_run.pull_requests is empty).",
    )
    _add_provider_arg(explain)
    explain.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="Cap on agent model round-trips before stopping gracefully.",
    )
    explain.add_argument(
        "--token-budget",
        type=int,
        default=None,
        help="Cap on cumulative input+output tokens before stopping gracefully.",
    )

    analyze = sub.add_parser(
        "analyze",
        help="Analyze logs from a file or stdin locally — no GitHub, no posting.",
    )
    analyze.add_argument("--logs", required=True, help="Path to a log file, or - for stdin.")
    analyze.add_argument("--diff", default=None, help="Optional path to a unified-diff file.")
    analyze.add_argument(
        "--no-ai",
        action="store_true",
        help="Deterministic rule-based summary with no model call (offline fallback).",
    )
    analyze.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format: text (default) or json (machine-readable verdict).",
    )
    _add_provider_arg(analyze)
    analyze.add_argument(
        "--max-turns", type=int, default=None, help="AI mode: cap model round-trips."
    )
    analyze.add_argument(
        "--token-budget", type=int, default=None, help="AI mode: cap cumulative tokens."
    )

    args = parser.parse_args(argv)

    if args.command == "analyze":
        logs = _read_text(args.logs)
        diff = _read_text(args.diff) if args.diff else None
        if args.no_ai:
            if args.format == "json":
                print(json.dumps(_logs_only_json(logs)))
            else:
                print(local.summarize(logs, diff))
            return 0
        if not _require_key(args.provider):
            return 2
        body = local.analyze_with_ai(
            logs,
            diff,
            provider=args.provider,
            model=args.model,
            max_turns=args.max_turns,
            token_budget=args.token_budget,
        )
        if args.format == "json":
            verdict = tools.last_verdict()
            print(json.dumps(_verdict_to_json(verdict) if verdict else _logs_only_json(logs)))
        else:
            print(body)
        return 0

    if args.command == "explain":
        if not _require_key(args.provider):
            return 2
        missing = [v for v in ("GITHUB_TOKEN", "GITHUB_REPOSITORY") if not os.environ.get(v)]
        if missing:
            print(
                f"{', '.join(missing)} not set. `misfire explain` needs GitHub "
                "credentials (set automatically inside the Action). For local "
                "analysis without GitHub, use `misfire analyze`.",
                file=sys.stderr,
            )
            return 2
        try:
            client = _github_client()
            pr_number = args.pr
            if pr_number is None:
                pr_number = client.find_pr_number_for_run(args.run)
                if pr_number is None:
                    print(
                        f"No pull request found for run {args.run}; nothing to comment on.",
                        file=sys.stderr,
                    )
                    return 0
            limits = {"model": providers.resolve_model(args.provider, args.model)}
            if args.max_turns is not None:
                limits["max_turns"] = args.max_turns
            if args.token_budget is not None:
                limits["token_budget"] = args.token_budget
            run_fn = providers.get_run(args.provider)
            body = run_fn(run_id=args.run, pr_number=pr_number, client=client, **limits)
            _emit_action_outputs(tools.last_verdict())
        except Exception as exc:
            # Misfire is advisory-only: an operational failure here — provider
            # rate-limit/outage, GitHub API error, expired logs, a missing
            # provider SDK — must never fail the CI step. Report the cause to
            # stderr (visible in the Action log) and exit 0 so the build status
            # is unchanged and no downstream webhook fires.
            print(
                f"Misfire could not complete the investigation for run {args.run}: "
                f"{type(exc).__name__}: {exc}. No advisory was posted; your build "
                "status is unchanged (Misfire never fails your CI).",
                file=sys.stderr,
            )
            return 0
        # Only a real advisory (it carries the hidden marker) goes to stdout, which
        # the Action captures as its `advisory` output. Diagnostics — "nothing to
        # post", turn/token-limit stops — go to stderr so they don't trigger a
        # downstream Slack/webhook notification.
        if MARKER in body:
            print(body)
        elif body:
            print(body, file=sys.stderr)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
