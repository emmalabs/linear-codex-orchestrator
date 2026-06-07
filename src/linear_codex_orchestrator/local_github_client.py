from __future__ import annotations

import json
import re
import subprocess
import tempfile

from .models import OpenPullRequest, PullRequest, PullRequestApproval, PullRequestFeedback


PR_FEEDBACK_COMMENT_MARKER = "<!-- codex-pr-feedback-worker -->"


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
                ]
            )
        pr_url = url.strip()
        return PullRequest(pull_request_number_from_url(pr_url), pr_url, title)

    async def list_open_prs(
        self,
        repo: str,
        *,
        branch_prefix: str,
    ) -> list[OpenPullRequest]:
        raw = _run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--limit",
                "1000",
                "--json",
                "number,url,title,headRefName,baseRefName",
            ]
        )
        prs = json.loads(raw)
        return [
            OpenPullRequest(
                repo=repo,
                number=item["number"],
                url=item["url"],
                title=item["title"],
                head_branch=item["headRefName"],
                base_branch=item["baseRefName"],
            )
            for item in prs
            if item.get("headRefName", "").startswith(branch_prefix)
        ]

    async def list_merged_prs(
        self,
        repo: str,
        *,
        branch_prefix: str,
    ) -> list[OpenPullRequest]:
        raw = _run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                "merged",
                "--limit",
                "1000",
                "--json",
                "number,url,title,headRefName,baseRefName",
            ]
        )
        prs = json.loads(raw)
        return [
            OpenPullRequest(
                repo=repo,
                number=item["number"],
                url=item["url"],
                title=item["title"],
                head_branch=item["headRefName"],
                base_branch=item["baseRefName"],
            )
            for item in prs
            if item.get("headRefName", "").startswith(branch_prefix)
        ]

    async def pr_feedback(self, repo: str, number: int) -> list[PullRequestFeedback]:
        return [
            feedback
            for feedback in (
                self._issue_comments(repo, number)
                + self._review_comments(repo, number)
                + self._reviews(repo, number)
            )
            if feedback.body.strip() and PR_FEEDBACK_COMMENT_MARKER not in feedback.body
        ]

    async def pr_codex_approvals(self, repo: str, number: int) -> list[PullRequestApproval]:
        return codex_approval_reviews(
            _gh_api_json(f"repos/{repo}/pulls/{number}/reviews?per_page=100")
        )

    async def comment_on_pr(self, repo: str, number: int, body: str) -> None:
        if self._dry_run:
            print(f"[dry-run] Would comment on {repo}#{number}:\n{body}")
            return
        with tempfile.NamedTemporaryFile("w+", suffix=".md") as body_file:
            body_file.write(f"{PR_FEEDBACK_COMMENT_MARKER}\n{body}")
            body_file.flush()
            _run(
                [
                    "gh",
                    "pr",
                    "comment",
                    str(number),
                    "--repo",
                    repo,
                    "--body-file",
                    body_file.name,
                ]
            )

    async def pr_archive_status(self, repo: str, number: int) -> str:
        try:
            pr = _gh_api_json(f"repos/{repo}/pulls/{number}")[0]
        except Exception:
            return "Archived"
        if pr.get("state") == "closed":
            return "Merged" if pr.get("merged") else "Closed"
        return "Archived"

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

    def _issue_comments(self, repo: str, number: int) -> list[PullRequestFeedback]:
        comments = _gh_api_json(f"repos/{repo}/issues/{number}/comments?per_page=100")
        return [
            PullRequestFeedback(
                key=f"issue-comment:{item['id']}:{item.get('updated_at') or item.get('created_at')}",
                kind="issue comment",
                author=item.get("user", {}).get("login", "unknown"),
                body=item.get("body") or "",
                url=item.get("html_url") or "",
            )
            for item in comments
        ]

    def _review_comments(self, repo: str, number: int) -> list[PullRequestFeedback]:
        comments = _gh_api_json(f"repos/{repo}/pulls/{number}/comments?per_page=100")
        return [
            PullRequestFeedback(
                key=f"review-comment:{item['id']}:{item.get('updated_at') or item.get('created_at')}",
                kind="review comment",
                author=item.get("user", {}).get("login", "unknown"),
                body=item.get("body") or "",
                url=item.get("html_url") or "",
                path=item.get("path"),
            )
            for item in comments
        ]

    def _reviews(self, repo: str, number: int) -> list[PullRequestFeedback]:
        reviews = _gh_api_json(f"repos/{repo}/pulls/{number}/reviews?per_page=100")
        feedback: list[PullRequestFeedback] = []
        for item in reviews:
            body = item.get("body") or ""
            state = item.get("state") or "REVIEW"
            if is_codex_approval_review(item):
                continue
            if not body.strip() and state != "CHANGES_REQUESTED":
                continue
            feedback.append(
                PullRequestFeedback(
                    key=f"review:{item['id']}:{item.get('submitted_at') or item.get('commit_id')}",
                    kind=f"review {state}",
                    author=item.get("user", {}).get("login", "unknown"),
                    body=body or "Reviewer requested changes.",
                    url=item.get("html_url") or "",
                )
            )
        return feedback


def is_codex_approval_review(item: dict[str, object]) -> bool:
    state = str(item.get("state") or "").upper()
    body = str(item.get("body") or "")
    return state == "APPROVED" and "👍" in body


def codex_approval_reviews(items: list[dict[str, object]]) -> list[PullRequestApproval]:
    approvals: list[PullRequestApproval] = []
    for item in items:
        if not is_codex_approval_review(item):
            continue
        submitted_at = str(item.get("submitted_at") or "")
        commit_id = str(item.get("commit_id") or "")
        user = item.get("user") or {}
        author = str(user.get("login", "unknown")) if isinstance(user, dict) else "unknown"
        approvals.append(
            PullRequestApproval(
                key=f"review:{item['id']}:{submitted_at or commit_id}",
                author=author,
                submitted_at=submitted_at,
                url=str(item.get("html_url") or ""),
                body=str(item.get("body") or ""),
            )
        )
    return approvals


def _run(command: list[str]) -> str:
    result = subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return result.stdout.strip()


def pull_request_number_from_url(url: str) -> int:
    match = re.search(r"/pull/(\d+)(?:$|[/?#])", url)
    return int(match.group(1)) if match else 0


def _gh_api_json(path: str) -> list[dict[str, object]]:
    raw = _run(["gh", "api", path])
    if not raw:
        return []
    payload = json.loads(raw)
    if isinstance(payload, list):
        return payload
    return [payload]
