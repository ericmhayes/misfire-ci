"""Unit tests for the GitHub REST wrapper — fully offline via httpx.MockTransport.

No network and no real token: every request is intercepted by a handler that
asserts on the outgoing request and returns a canned response. This exercises the
*live* path's request-building and response-parsing without hitting GitHub.
"""

from __future__ import annotations

import io
import zipfile

import httpx
import pytest

from misfire.github_client import GitHubClient

TOKEN = "ghs_faketoken"
REPO = "ericmhayes/misfire-ci"


def _client(handler) -> GitHubClient:
    """A GitHubClient whose network is replaced by `handler`."""
    return GitHubClient(token=TOKEN, repo=REPO, transport=httpx.MockTransport(handler))


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def test_fetch_pr_diff_sends_diff_accept_and_returns_text() -> None:
    diff = "--- a/auth.py\n+++ b/auth.py\n@@ -1 +1 @@\n-old\n+new\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == f"/repos/{REPO}/pulls/7"
        assert request.headers["Accept"] == "application/vnd.github.diff"
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(200, text=diff)

    assert _client(handler).fetch_pr_diff(7) == diff


def test_create_pr_comment_posts_body_and_returns_url() -> None:
    url = "https://github.com/ericmhayes/misfire-ci/pull/7#issuecomment-42"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == f"/repos/{REPO}/issues/7/comments"
        import json

        assert json.loads(request.content) == {"body": "hello"}
        return httpx.Response(201, json={"html_url": url})

    assert _client(handler).create_pr_comment(7, "hello") == url


def _jobs(*jobs: dict) -> dict:
    return {"jobs": list(jobs)}


def test_fetch_failing_logs_fetches_only_failed_jobs() -> None:
    # A matrix run: one leg failed, one passed. Only the failed leg's logs should
    # be fetched — the passing leg must never be requested.
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/repos/{REPO}/actions/runs/99/jobs":
            assert request.url.params["filter"] == "latest"
            return httpx.Response(
                200,
                json=_jobs(
                    {"id": 101, "name": "test (3.11)", "conclusion": "failure"},
                    {"id": 102, "name": "test (3.12)", "conclusion": "success"},
                ),
            )
        if path == f"/repos/{REPO}/actions/jobs/101/logs":
            return httpx.Response(200, text="E   AssertionError: boom on 3.11\n")
        raise AssertionError(f"unexpected request to {path}")  # passing job not fetched

    out = _client(handler).fetch_failing_logs(99)
    assert "boom on 3.11" in out
    assert "===== failed job: test (3.11) =====" in out
    assert "3.12" not in out  # the passing leg is absent


def test_fetch_failing_logs_truncates_each_job() -> None:
    big = "\n".join(
        ["E   AssertionError: boom near the top"] + [f"filler line {i}" for i in range(5000)]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs"):
            job = {"id": 7, "name": "test", "conclusion": "failure"}
            return httpx.Response(200, json=_jobs(job))
        return httpx.Response(200, text=big)

    out = _client(handler).fetch_failing_logs(99)
    assert out.count("\n") < 1000  # trimmed well below the 5000-line input
    assert "E   AssertionError: boom near the top" in out  # first error rescued
    assert "filler line 4999" in out  # failing tail preserved
    assert "truncated" in out


def test_fetch_failing_logs_follows_redirect_to_download() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs"):
            job = {"id": 7, "name": "test", "conclusion": "failure"}
            return httpx.Response(200, json=_jobs(job))
        if request.url.path.endswith("/jobs/7/logs"):
            return httpx.Response(302, headers={"Location": "https://blob.example/j.txt"})
        assert request.url.host == "blob.example"
        return httpx.Response(200, text="boom\n")

    assert "boom" in _client(handler).fetch_failing_logs(99)


def test_fetch_failing_logs_falls_back_to_run_archive() -> None:
    # No failed jobs reported (older runner / API gap) → fall back to the whole
    # run log archive so a diagnosis is still possible.
    archive = _zip_bytes(
        {
            "build/1_step.txt": "compiling...\n",
            "test/2_step.txt": "E AssertionError\n",
            "empty_dir/": "",  # directory entry, must be skipped
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json=_jobs())  # no jobs → fallback
        assert request.url.path == f"/repos/{REPO}/actions/runs/99/logs"
        return httpx.Response(200, content=archive)

    out = _client(handler).fetch_failing_logs(99)
    assert "compiling..." in out
    assert "E AssertionError" in out
    assert "===== build/1_step.txt =====" in out


def test_fetch_failing_logs_caps_number_of_failed_jobs() -> None:
    # More failed legs than the per-run cap: the overflow is noted, not fetched.
    many = [{"id": 200 + i, "name": f"leg-{i}", "conclusion": "failure"} for i in range(8)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json=_jobs(*many))
        job_id = int(request.url.path.split("/jobs/")[1].split("/logs")[0])
        assert job_id < 205, "a job beyond the cap was fetched"  # only first 5 (200-204)
        return httpx.Response(200, text=f"fail {job_id}\n")

    out = _client(handler).fetch_failing_logs(99)
    assert "more failed job(s) omitted" in out


def test_get_run_parses_workflow_and_sha() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/repos/{REPO}/actions/runs/55"
        return httpx.Response(
            200, json={"id": 55, "workflow_id": 900, "head_sha": "abc123", "conclusion": "failure"}
        )

    run = _client(handler).get_run(55)
    assert (run.run_id, run.workflow_id, run.head_sha, run.conclusion) == (
        55,
        900,
        "abc123",
        "failure",
    )


def test_list_workflow_runs_filters_completed_and_maps_refs() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/repos/{REPO}/actions/workflows/900/runs"
        assert request.url.params["status"] == "completed"
        assert request.url.params["per_page"] == "5"
        return httpx.Response(
            200,
            json={
                "workflow_runs": [
                    {"id": 3, "workflow_id": 900, "head_sha": "sha3", "conclusion": "failure"},
                    {"id": 2, "workflow_id": 900, "head_sha": "sha2", "conclusion": "success"},
                ]
            },
        )

    runs = _client(handler).list_workflow_runs(900, limit=5)
    assert [r.run_id for r in runs] == [3, 2]
    assert runs[0].head_sha == "sha3"


MARKER = "<!-- misfire:advisory -->"


def test_upsert_creates_new_when_no_marked_comment_exists() -> None:
    url = "https://github.com/ericmhayes/misfire-ci/pull/7#issuecomment-99"
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        if request.method == "GET":
            assert request.url.path == f"/repos/{REPO}/issues/7/comments"
            if request.url.params.get("page") != "1":
                return httpx.Response(200, json=[])  # exhausted → stop paginating
            return httpx.Response(200, json=[{"id": 1, "body": "a human comment"}])
        assert request.method == "POST"
        assert request.url.path == f"/repos/{REPO}/issues/7/comments"
        import json

        assert MARKER in json.loads(request.content)["body"]
        return httpx.Response(201, json={"html_url": url})

    out = _client(handler).upsert_pr_comment(7, f"{MARKER}\nbody", marker=MARKER)
    assert out == url
    assert seen == ["GET", "GET", "POST"]  # page 1 (no match), page 2 (empty), then POST


def test_upsert_updates_existing_bot_marked_comment() -> None:
    url = "https://github.com/ericmhayes/misfire-ci/pull/7#issuecomment-5"
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {"id": 11, "body": "unrelated human comment", "user": {"type": "User"}},
                    {"id": 12, "body": f"{MARKER}\nstale advisory", "user": {"type": "Bot"}},
                ],
            )
        assert request.method == "PATCH"
        # Updates the marked comment by id — no new comment is posted.
        assert request.url.path == f"/repos/{REPO}/issues/comments/12"
        return httpx.Response(200, json={"html_url": url})

    out = _client(handler).upsert_pr_comment(7, f"{MARKER}\nfresh", marker=MARKER)
    assert out == url
    assert seen == ["GET", "PATCH"]  # no POST


def test_upsert_ignores_a_humans_marker_comment() -> None:
    # A human pastes the hidden marker — it must NOT be hijacked/updated.
    url = "https://github.com/ericmhayes/misfire-ci/pull/7#issuecomment-new"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            if request.url.params.get("page") != "1":
                return httpx.Response(200, json=[])
            return httpx.Response(
                200,
                json=[{"id": 99, "body": f"{MARKER} nice try", "user": {"type": "User"}}],
            )
        assert request.method == "POST"  # posts a fresh comment, doesn't PATCH id 99
        return httpx.Response(201, json={"html_url": url})

    assert _client(handler).upsert_pr_comment(7, f"{MARKER}\nbody", marker=MARKER) == url


def test_find_pr_number_prefers_open_pr() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/repos/{REPO}/actions/runs/55":
            return httpx.Response(
                200,
                json={"id": 55, "workflow_id": 900, "head_sha": "abc123", "conclusion": "failure"},
            )
        assert request.url.path == f"/repos/{REPO}/commits/abc123/pulls"
        return httpx.Response(
            200,
            json=[
                {"number": 3, "state": "closed"},
                {"number": 4, "state": "open"},
            ],
        )

    assert _client(handler).find_pr_number_for_run(55) == 4


def test_find_pr_number_is_none_when_commit_has_no_pr() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/actions/runs/55"):
            return httpx.Response(
                200,
                json={"id": 55, "workflow_id": 900, "head_sha": "abc123", "conclusion": "failure"},
            )
        assert request.url.path == f"/repos/{REPO}/commits/abc123/pulls"
        return httpx.Response(200, json=[])

    assert _client(handler).find_pr_number_for_run(55) is None


def test_http_error_propagates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler).fetch_pr_diff(123)
