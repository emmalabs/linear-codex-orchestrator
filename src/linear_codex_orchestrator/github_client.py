from __future__ import annotations

from typing import Any

import httpx

from .models import PullRequest


class GitHubClient:
    def __init__(self, token: str, dry_run: bool = False) -> None:
        self._dry_run = dry_run
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def create_or_update_pr(
        self,
        repo: str,
        branch: str,
        base: str,
        title: str,
        body: str,
    ) -> PullRequest:
        existing = await self._find_open_pr(repo, branch)
        if existing:
            if not self._dry_run:
                await self._client.patch(
                    f"/repos/{repo}/pulls/{existing['number']}",
                    json={"title": title, "body": body},
                )
            return PullRequest(existing["number"], existing["html_url"], title)
        if self._dry_run:
            return PullRequest(0, f"https://github.com/{repo}/compare/{base}...{branch}", title)
        response = await self._client.post(
            f"/repos/{repo}/pulls",
            json={"title": title, "head": branch, "base": base, "body": body, "draft": True},
        )
        response.raise_for_status()
        data = response.json()
        return PullRequest(data["number"], data["html_url"], data["title"])

    async def _find_open_pr(self, repo: str, branch: str) -> dict[str, Any] | None:
        owner = repo.split("/", 1)[0]
        response = await self._client.get(
            f"/repos/{repo}/pulls",
            params={"state": "open", "head": f"{owner}:{branch}"},
        )
        response.raise_for_status()
        prs = response.json()
        return prs[0] if prs else None

