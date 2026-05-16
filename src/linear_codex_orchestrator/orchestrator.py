from __future__ import annotations

import asyncio
from pathlib import Path

from .codex_cli import run_codex
from .config import RepoConfig, Settings
from .git_ops import branch_name, commit_all, ensure_branch, has_changes, push_branch, run_git
from .local_github_client import LocalGitHubClient
from .local_linear_client import LocalLinearClient
from .locks import lock_for_repo
from .models import LinearIssue, ReviewResult


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        linear: LocalLinearClient | None = None,
        github: LocalGitHubClient | None = None,
    ) -> None:
        self.settings = settings
        self.linear = linear or LocalLinearClient(
            Path.cwd(),
            dry_run=settings.dry_run,
            model=settings.codex_model,
            route_labels=self._repo_labels(settings),
        )
        self.github = github or LocalGitHubClient(dry_run=settings.dry_run)

    async def close(self) -> None:
        await self.linear.close()
        await self.github.close()

    async def run_once(self) -> None:
        issues = await self.linear.ready_issues(
            self.settings.todo_status,
            self.settings.ready_label,
            max(self.settings.max_issues_per_tick * 5, 5),
        )
        processed = 0
        for issue in issues:
            if processed >= self.settings.max_issues_per_tick:
                break
            if not self.can_resolve_repo(issue):
                print(
                    f"Skipping {issue.identifier}: add one repo label "
                    f"({', '.join(self.repo_labels())})"
                )
                continue
            await self.process_issue(issue)
            processed += 1

    async def run_forever(self, interval_seconds: int = 900) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(interval_seconds)

    async def process_issue(self, issue: LinearIssue) -> None:
        repo = self.resolve_repo(issue)
        with lock_for_repo(self.settings.lock_dir, repo.github) as lock:
            if not lock.acquired:
                print(f"Skipping {issue.identifier}: repo lock is already held for {repo.github}")
                return
            await self._process_locked_issue(issue, repo)

    def can_resolve_repo(self, issue: LinearIssue) -> bool:
        try:
            self.resolve_repo(issue)
        except RuntimeError:
            return False
        return True

    def repo_labels(self) -> list[str]:
        return self._repo_labels(self.settings)

    @staticmethod
    def _repo_labels(settings: Settings) -> list[str]:
        return sorted(repo.label for repo in settings.repo_map.values() if repo.label)

    def resolve_repo(self, issue: LinearIssue) -> RepoConfig:
        labeled_matches = [
            repo for repo in self.settings.repo_map.values() if repo.label in issue.labels
        ]
        if len(labeled_matches) == 1:
            return labeled_matches[0]
        if len(labeled_matches) > 1:
            labels = ", ".join(repo.label or "" for repo in labeled_matches)
            raise RuntimeError(f"Multiple repository labels matched {issue.identifier}: {labels}")
        try:
            return self.settings.repo_map[issue.team_key]
        except KeyError as exc:
            raise RuntimeError(
                f"No REPO_MAP_JSON entry for Linear team key {issue.team_key} "
                "and no repository label matched the issue."
            ) from exc

    async def _process_locked_issue(self, issue: LinearIssue, repo: RepoConfig) -> None:
        branch = branch_name(issue.identifier, issue.title)
        print(f"Processing {issue.identifier} on {repo.github}:{branch}")

        try:
            await self.linear.move_issue(issue.id, self.settings.in_progress_status)
            await self.linear.add_label(issue.id, self.settings.running_label)
            ensure_branch(repo.path, repo.base, branch)

            plan = await self._plan(issue, repo)
            await self._implement(issue, repo, plan)
            review = await self._review(issue, repo, plan)
        except Exception as exc:
            await self.linear.comment(issue.id, f"Codex orchestration failed:\n\n```text\n{exc}\n```")
            raise

        if not review.passed:
            await self.linear.comment(
                issue.id,
                f"Codex reviewer did not approve an automatic PR yet.\n\n{review.summary}",
            )
            print(f"Reviewer blocked {issue.identifier}")
            return

        if not has_changes(repo.path):
            await self.linear.comment(issue.id, "Codex completed the run, but no git changes exist.")
            print(f"No changes for {issue.identifier}")
            return

        commit_all(repo.path, f"{issue.identifier}: {issue.title}")
        if not self.settings.dry_run:
            push_branch(repo.path, branch)

        pr_body = pr_description(issue, repo.path, plan, review)
        pr = await self.github.create_or_update_pr(
            repo.github,
            branch,
            repo.base,
            f"{issue.identifier}: {issue.title}",
            pr_body,
        )
        await self.linear.comment(issue.id, f"Draft PR ready for review: {pr.url}")
        await self.linear.move_issue(issue.id, self.settings.in_review_status)
        print(f"Opened/updated PR for {issue.identifier}: {pr.url}")

    async def _plan(self, issue: LinearIssue, repo: RepoConfig) -> str:
        plan = run_codex(
            planner_prompt(issue, repo),
            repo.path,
            model=self.settings.codex_model,
            sandbox="read-only",
            timeout_seconds=900,
        )
        if "BLOCKED" in plan.upper():
            await self.linear.comment(issue.id, f"Planner blocked automatic implementation.\n\n{plan}")
            raise RuntimeError(f"Planner blocked {issue.identifier}")
        return plan

    async def _implement(self, issue: LinearIssue, repo: RepoConfig, plan: str) -> None:
        run_codex(
            implementation_prompt(issue, repo.path, plan),
            repo.path,
            model=self.settings.codex_model,
            sandbox=self.settings.codex_sandbox,
        )

    async def _review(self, issue: LinearIssue, repo: RepoConfig, plan: str) -> ReviewResult:
        summary = run_codex(
            review_prompt(issue, plan, self.settings.test_command),
            repo.path,
            model=self.settings.codex_model,
            sandbox="read-only",
            timeout_seconds=1800,
        )
        return ReviewResult(
            passed="REVIEW_DECISION: PASS" in summary,
            summary=summary,
            tests="See reviewer summary.",
        )


def issue_prompt(issue: LinearIssue, repo: RepoConfig) -> str:
    return f"""
Linear issue:
- ID: {issue.identifier}
- URL: {issue.url}
- Title: {issue.title}
- Team: {issue.team_key} ({issue.team_name})
- Repository: {repo.github}
- Base branch: {repo.base}

Description:
{issue.description}
""".strip()


def planner_prompt(issue: LinearIssue, repo: RepoConfig) -> str:
    return f"""
You are the planner for an automated software workflow.
Scope this Linear task before implementation. Summarize acceptance criteria,
likely files or areas, risks, and a compact implementation plan.
If the task is vague, sensitive, or unsafe for automation, say BLOCKED clearly.

{issue_prompt(issue, repo)}
""".strip()


def implementation_prompt(issue: LinearIssue, repo_path: Path, plan: str) -> str:
    return f"""
Implement this Linear issue in the repository at {repo_path}.

Issue: {issue.identifier} - {issue.title}
URL: {issue.url}

Planner scope:
{plan}

Requirements:
- Make focused code changes for the requested behavior.
- Add or update tests when the change warrants it.
- Do not push or create a pull request.
- Do not move or comment on Linear issues.
- Leave a clean working tree except for intentional changes.
""".strip()


def review_prompt(issue: LinearIssue, plan: str, test_command: str | None) -> str:
    test_instruction = (
        f'Run this test command and include the result: "{test_command}".'
        if test_command
        else "No TEST_COMMAND is configured; inspect the diff and run obvious lightweight checks if available."
    )
    return f"""
You are a strict read-only code reviewer. You may inspect files and run read-only
commands, but do not modify files.

Review the implementation for {issue.identifier}: {issue.title}.

Acceptance scope:
{plan}

Check:
- git status
- diff against HEAD
- {test_instruction}
- whether the changes satisfy the issue without unrelated edits

End with exactly one line containing REVIEW_DECISION: PASS or REVIEW_DECISION: FAIL,
followed by a concise rationale.
""".strip()


def pr_description(issue: LinearIssue, repo_path: Path, plan: str, review: ReviewResult) -> str:
    diffstat = ""
    try:
        diffstat = run_git(repo_path, "diff", "--stat", "HEAD~1..HEAD")
    except Exception:
        diffstat = "Diffstat unavailable."
    return f"""
Linear issue: {issue.url}

## Plan
{plan}

## Reviewer
{review.summary}

## Diffstat
```text
{diffstat}
```
""".strip()
