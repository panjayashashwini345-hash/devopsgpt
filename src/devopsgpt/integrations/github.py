"""GitHub integration — open a pull request via the REST API, or mock.

Live mode performs the real PR dance against ``GITHUB_REPO``:
  1. resolve the base branch's head SHA,
  2. create a new ref (branch) off it,
  3. commit the proposed change to a target file (Contents API),
  4. open a PR from the new branch into the base branch.

If a ``diff``/patch is supplied we attach it to the PR body for review (applying
arbitrary unified diffs server-side is out of scope for the boilerplate; the
Contents-API commit covers the common single-file fix).

Mock mode fabricates a deterministic PR number/url. Any live failure degrades to
mock rather than raising.
"""

from __future__ import annotations

import base64
from typing import Protocol

import httpx

from ..config import IntegrationMode, Settings
from ..logging import get_logger
from ..models import PullRequest

log = get_logger(__name__)


class GitHubAdapter(Protocol):
    async def create_pull_request(
        self,
        title: str,
        body: str,
        branch: str,
        *,
        base_branch: str | None = None,
        file_path: str | None = None,
        file_content: str | None = None,
        diff: str = "",
    ) -> PullRequest:
        ...

    async def aclose(self) -> None:
        ...


class MockGitHubAdapter:
    def __init__(self, settings: Settings) -> None:
        self._repo = settings.github_repo or "your-org/checkout-service"
        self._base = settings.github_default_base_branch
        self._counter = 41

    async def create_pull_request(
        self,
        title: str,
        body: str,
        branch: str,
        *,
        base_branch: str | None = None,
        file_path: str | None = None,
        file_content: str | None = None,
        diff: str = "",
    ) -> PullRequest:
        self._counter += 1
        number = self._counter
        log.info("github.mock_pr", repo=self._repo, number=number, branch=branch)
        return PullRequest(
            number=number,
            url=f"https://github.com/{self._repo}/pull/{number}",
            title=title,
            body=body,
            repo=self._repo,
            head_branch=branch,
            base_branch=base_branch or self._base,
            diff=diff,
            created=True,
            mocked=True,
        )

    async def aclose(self) -> None:
        return None


class LiveGitHubAdapter:
    def __init__(self, settings: Settings) -> None:
        self._repo = settings.github_repo
        self._base_default = settings.github_default_base_branch
        self._client = httpx.AsyncClient(
            base_url=settings.github_api_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {settings.github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=settings.http_timeout_s,
        )
        self._fallback = MockGitHubAdapter(settings)

    async def create_pull_request(
        self,
        title: str,
        body: str,
        branch: str,
        *,
        base_branch: str | None = None,
        file_path: str | None = None,
        file_content: str | None = None,
        diff: str = "",
    ) -> PullRequest:
        base = base_branch or self._base_default
        try:
            head_sha = await self._branch_sha(base)
            await self._create_branch(branch, head_sha)
            if file_path and file_content is not None:
                await self._commit_file(branch, file_path, file_content, title)
            pr = await self._open_pr(title, body, branch, base, diff)
            return pr
        except httpx.HTTPError as exc:
            log.warning("github.live_pr_failed", error=str(exc), fallback="mock")
            return await self._fallback.create_pull_request(
                title, body, branch, base_branch=base, diff=diff
            )

    async def _branch_sha(self, branch: str) -> str:
        resp = await self._client.get(f"/repos/{self._repo}/git/ref/heads/{branch}")
        resp.raise_for_status()
        return resp.json()["object"]["sha"]

    async def _create_branch(self, branch: str, sha: str) -> None:
        resp = await self._client.post(
            f"/repos/{self._repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        # 422 => branch already exists; tolerate it.
        if resp.status_code not in (201, 422):
            resp.raise_for_status()

    async def _commit_file(self, branch: str, path: str, content: str, message: str) -> None:
        # Need the current blob sha if the file already exists.
        existing = await self._client.get(
            f"/repos/{self._repo}/contents/{path}", params={"ref": branch}
        )
        sha = existing.json().get("sha") if existing.status_code == 200 else None
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        resp = await self._client.put(f"/repos/{self._repo}/contents/{path}", json=payload)
        resp.raise_for_status()

    async def _open_pr(
        self, title: str, body: str, head: str, base: str, diff: str
    ) -> PullRequest:
        full_body = body
        if diff:
            full_body = f"{body}\n\n<details><summary>Proposed diff</summary>\n\n```diff\n{diff}\n```\n\n</details>"
        resp = await self._client.post(
            f"/repos/{self._repo}/pulls",
            json={"title": title, "body": full_body, "head": head, "base": base, "draft": True},
        )
        resp.raise_for_status()
        data = resp.json()
        log.info("github.live_pr", repo=self._repo, number=data["number"])
        return PullRequest(
            number=data["number"],
            url=data["html_url"],
            title=title,
            body=full_body,
            repo=self._repo,
            head_branch=head,
            base_branch=base,
            diff=diff,
            created=True,
            mocked=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._fallback.aclose()


def build_github_adapter(settings: Settings) -> GitHubAdapter:
    if settings.effective_github_mode() is IntegrationMode.LIVE:
        return LiveGitHubAdapter(settings)
    if settings.github_mode is IntegrationMode.LIVE:
        log.warning("github.live_requested_without_creds", fallback="mock")
    return MockGitHubAdapter(settings)
