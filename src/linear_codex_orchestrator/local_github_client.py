from __future__ import annotations

import json
import subprocess
import tempfile

from .models import PullRequest


class LocalGitHubClient:
    def __init__(self, dry_run: bool = False) -> None:
        self._dry_run = dry_run

    async def close(self) -> None:
        return None

    async def create_or_update_pr(
        self,
        repo: str,
        branch: str,
        base: str,
        title: str,
        body: str,
    ) -> PullRequest:
        existing = self._find_open_pr(repo, branch)
        if existing:
            if not self._dry_run:
                with tempfile.NamedTemporaryFile("w+", suffix=".md") as body_file:
                    body_file.write(body)
                    body_file.flush()
                    _run(
                        [
                            "gh",
                            "pr",
                            "edit",
                            str(existing["number"]),
                            "--repo",
                            repo,
                            "--title",
                            title,
                            "--body-file",
                            body_file.name,
                        ]
                    )
            return PullRequest(existing["number"], existing["url"], title)
        if self._dry_run:
            return PullRequest(0, f"https://github.com/{repo}/compare/{base}...{branch}", title)
        with tempfile.NamedTemporaryFile("w+", suffix=".md") as body_file:
            body_file.write(body)
            body_file.flush()
            url = _run(
                [
                    "gh",
                    "pr",
                    "create",
                    "--repo",
                    repo,
                    "--base",
                    base,
                    "--head",
                    branch,
                    "--title",
                    title,
                    "--body-file",
                    body_file.name,
                    "--draft",
                ]
            )
        return PullRequest(0, url.strip(), title)

    def _find_open_pr(self, repo: str, branch: str) -> dict[str, object] | None:
        raw = _run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--head",
                branch,
                "--state",
                "open",
                "--limit",
                "1",
                "--json",
                "number,url,title",
            ]
        )
        prs = json.loads(raw)
        return prs[0] if prs else None


def _run(command: list[str]) -> str:
    result = subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return result.stdout.strip()

