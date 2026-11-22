"""All GitHub REST I/O lives here, isolated so it can be faked in tests.

Nothing else in the package should import `httpx` or hit the network directly.
The shared tools in `tools.py` call into this module; the tools are what
the offline test harness mocks, so these functions are only exercised on the live path.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

import httpx

from .log_utils import truncate_logs

_API_ROOT = "https://api.github.com"

# Cap on how many failed jobs' logs we pull into one investigation (cost guard;
# a run with many failed matrix legs would otherwise fetch them all).
_MAX_FAILED_JOBS = 5


@dataclass(frozen=True)
class RunRef:
    """Lightweight reference to a workflow run (used for flaky-history lookups)."""

    run_id: int
    workflow_id: int
    head_sha: str
    conclusion: str  # "success", "failure", "cancelled", ... ("" if still running)
    head_branch: str = ""  # branch the run built; identifies the PR under test
    run_attempt: int = 1  # attempt number; >1 means the run was re-run


@dataclass
class GitHubClient:
    """Thin wrapper over the GitHub REST API for a single repo."""

    token: str
    repo: str  # "owner/name"
    transport: httpx.BaseTransport | None = None  # test seam; None = real network

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=_API_ROOT,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
            # The logs endpoint 302s to a signed download URL; follow it.
            follow_redirects=True,
            transport=self.transport,
        )

    def _download_logs_zip(self, path: str) -> str:
        """Fetch a log-archive endpoint and concatenate its per-step files.

        The API returns a zip of per-step log files; we concatenate them, each
        prefixed with its path.
        """
        with self._client() as client:
            resp = client.get(path)
            resp.raise_for_status()
        archive = zipfile.ZipFile(io.BytesIO(resp.content))
        parts = []
        for name in sorted(archive.namelist()):
            if name.endswith("/"):  # directory entry
                continue
            text = archive.read(name).decode("utf-8", errors="replace")
            parts.append(f"===== {name} =====\n{text}")
        return "\n".join(parts)

    def _download_run_logs(self, run_id: int) -> str:
        """Raw per-step logs for a run's latest attempt (untruncated).

        Kept separate from `fetch_failing_logs` so the flaky-history path can
        search full logs for a test id without the model-facing truncation.
        """
        return self._download_logs_zip(f"/repos/{self.repo}/actions/runs/{run_id}/logs")

    def download_run_attempt_logs(self, run_id: int, attempt: int) -> str:
        """Raw per-step logs for a *specific earlier attempt* of a run.

        Used to detect a fail→pass retry: compare a prior attempt (which may have
        failed a test) against the latest attempt (which succeeded).
        """
        return self._download_logs_zip(
            f"/repos/{self.repo}/actions/runs/{run_id}/attempts/{attempt}/logs"
        )

    def list_failed_jobs(self, run_id: int) -> list[tuple[int, str]]:
        """Return `(job_id, job_name)` for the run's jobs that *failed*.

        Uses the latest attempt of each job. A run can contain many jobs (matrix
        legs, lint/test/build) and usually only some fail; isolating the failed
        ones is what keeps `fetch_failing_logs` from mistaking a *passing* job's
        output for the failure.
        """
        with self._client() as client:
            resp = client.get(
                f"/repos/{self.repo}/actions/runs/{run_id}/jobs",
                params={"filter": "latest", "per_page": 100},
            )
            resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
        return [
            (j["id"], j.get("name") or f"job {j['id']}")
            for j in jobs
            if (j.get("conclusion") or "") == "failure"
        ]

    def fetch_job_logs(self, job_id: int) -> str:
        """Fetch the raw logs for a single job (untruncated)."""
        with self._client() as client:
            resp = client.get(f"/repos/{self.repo}/actions/jobs/{job_id}/logs")
            resp.raise_for_status()
        return resp.text

    def fetch_failing_logs(self, run_id: int) -> str:
        """Return the logs of the run's *failing jobs*, trimmed for the model.

        Fetches only the jobs that actually failed (so on a matrix build the
        model sees the broken leg, not a green one), labels each, and runs each
        through `log_utils.truncate_logs` so a multi-MB CI log never lands in the
        model context wholesale (cost guardrail). Falls back to the whole-run log
        archive when the jobs API yields nothing usable (older runners, API
        hiccup) so a diagnosis is still possible.
        """
        try:
            failed = self.list_failed_jobs(run_id)
        except Exception:
            failed = []

        parts: list[str] = []
        for job_id, name in failed[:_MAX_FAILED_JOBS]:
            try:
                logs = truncate_logs(self.fetch_job_logs(job_id))
            except Exception:
                continue  # skip a job whose logs are unavailable; keep the rest
            parts.append(f"===== failed job: {name} =====\n{logs}")

        if not parts:
            # No per-job logs available — fall back to the whole-run archive.
            return truncate_logs(self._download_run_logs(run_id))

        if len(failed) > _MAX_FAILED_JOBS:
            parts.append(
                f"... [{len(failed) - _MAX_FAILED_JOBS} more failed job(s) omitted] ..."
            )
        return "\n\n".join(parts)

    def get_run(self, run_id: int) -> RunRef:
        """Fetch metadata (workflow, head SHA, conclusion) for a single run."""
        with self._client() as client:
            resp = client.get(f"/repos/{self.repo}/actions/runs/{run_id}")
            resp.raise_for_status()
        data = resp.json()
        return RunRef(
            run_id=data["id"],
            workflow_id=data["workflow_id"],
            head_sha=data["head_sha"],
            conclusion=data.get("conclusion") or "",
            head_branch=data.get("head_branch") or "",
            run_attempt=data.get("run_attempt") or 1,
        )

    def list_workflow_runs(self, workflow_id: int, *, limit: int = 20) -> list[RunRef]:
        """Return the most recent completed runs of a workflow, newest first.

        Bounded by `limit`: flaky detection samples a recent window rather
        than the whole history. Only completed runs are returned so in-flight
        runs don't skew the pass/fail tally.
        """
        with self._client() as client:
            resp = client.get(
                f"/repos/{self.repo}/actions/workflows/{workflow_id}/runs",
                params={"status": "completed", "per_page": limit},
            )
            resp.raise_for_status()
        runs = resp.json().get("workflow_runs", [])
        return [
            RunRef(
                run_id=r["id"],
                workflow_id=r["workflow_id"],
                head_sha=r["head_sha"],
                conclusion=r.get("conclusion") or "",
                head_branch=r.get("head_branch") or "",
                run_attempt=r.get("run_attempt") or 1,
            )
            for r in runs
        ]

    def fetch_pr_diff(self, pr_number: int) -> str:
        """Return the unified diff for a pull request."""
        with self._client() as client:
            resp = client.get(
                f"/repos/{self.repo}/pulls/{pr_number}",
                headers={"Accept": "application/vnd.github.diff"},
            )
            resp.raise_for_status()
        return resp.text

    def create_pr_comment(self, pr_number: int, body: str) -> str:
        """Post an issue comment on the PR; return the created comment URL."""
        with self._client() as client:
            resp = client.post(
                f"/repos/{self.repo}/issues/{pr_number}/comments",
                json={"body": body},
            )
            resp.raise_for_status()
        return resp.json()["html_url"]

    def upsert_pr_comment(self, pr_number: int, body: str, *, marker: str) -> str:
        """Create or update the single advisory comment on the PR.

        Finds a prior **bot-authored** comment whose body carries `marker` (a
        hidden HTML marker Misfire embeds in every advisory) and PATCHes it;
        otherwise posts a new one. Requiring the author to be a Bot stops a human
        who pastes the marker from hijacking the advisory. Scans paginated
        comments (bounded) so the upsert still finds it on busy PRs. Returns the
        comment URL.
        """
        with self._client() as client:
            existing = None
            page = 1
            while existing is None and page <= 10:
                resp = client.get(
                    f"/repos/{self.repo}/issues/{pr_number}/comments",
                    params={"per_page": 100, "page": page},
                )
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    break
                existing = next(
                    (
                        c
                        for c in batch
                        if marker in (c.get("body") or "")
                        and (c.get("user") or {}).get("type") == "Bot"
                    ),
                    None,
                )
                page += 1
            if existing is not None:
                updated = client.patch(
                    f"/repos/{self.repo}/issues/comments/{existing['id']}",
                    json={"body": body},
                )
                updated.raise_for_status()
                return updated.json()["html_url"]
            created = client.post(
                f"/repos/{self.repo}/issues/{pr_number}/comments",
                json={"body": body},
            )
            created.raise_for_status()
        return created.json()["html_url"]

    def find_pr_number_for_run(self, run_id: int) -> int | None:
        """Resolve the PR a failed run belongs to, via the run's head commit.

        `workflow_run.pull_requests` is empty for PRs from forks, so the Action
        can't rely on it. The commit→pulls endpoint works for fork PRs too (the
        PR lives in the base repo), so we resolve from the run's head SHA.
        Prefers an open PR; returns None when the commit has no associated PR
        (e.g. a direct push to a branch).
        """
        head_sha = self.get_run(run_id).head_sha
        with self._client() as client:
            resp = client.get(f"/repos/{self.repo}/commits/{head_sha}/pulls")
            resp.raise_for_status()
        pulls = resp.json()
        if not pulls:
            return None
        for pr in pulls:
            if pr.get("state") == "open":
                return pr["number"]
        return pulls[0]["number"]
