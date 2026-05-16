from __future__ import annotations

import asyncio
from pathlib import Path

from .codex_cli import run_codex
from .config import RepoConfig, Settings, WorkspaceConfig
from .git_ops import branch_name, changed_files, commit_all, ensure_branch, has_changes, push_branch, run_git
from .local_github_client import LocalGitHubClient
from .local_linear_client import LocalLinearClient
from .locks import lock_for_repo
from .models import LinearIssue, PullRequest, ReviewResult


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
        )
        self.github = github or LocalGitHubClient(dry_run=settings.dry_run)

    async def close(self) -> None:
        await self.linear.close()
        await self.github.close()

    async def run_once(self) -> None:
        issues = await self.linear.ready_issues(
            self.settings.todo_status,
            self.settings.ready_label,
            self.settings.max_issues_per_tick,
        )
        for issue in issues:
            await self.process_issue(issue)

    async def run_forever(self, interval_seconds: int = 900) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(interval_seconds)

    async def process_issue(self, issue: LinearIssue) -> None:
        workspace = self.resolve_workspace(issue)
        lock_name = f"{issue.team_key}:{workspace.path}"
        with lock_for_repo(self.settings.lock_dir, lock_name) as lock:
            if not lock.acquired:
                print(f"Skipping {issue.identifier}: workspace lock is already held")
                return
            await self._process_locked_issue(issue, workspace)

    def resolve_workspace(self, issue: LinearIssue) -> WorkspaceConfig:
        normalized_key = issue.team_key.upper()
        try:
            return self.settings.workspace_map[normalized_key]
        except KeyError as exc:
            raise RuntimeError(
                f"No WORKSPACE_MAP_JSON entry for Linear team key {issue.team_key}."
            ) from exc

    async def _process_locked_issue(self, issue: LinearIssue, workspace: WorkspaceConfig) -> None:
        branch = branch_name(issue.identifier, issue.title)
        repo_list = ", ".join(workspace.repos)
        print(f"Processing {issue.identifier} in {workspace.path} across: {repo_list}")
        if self.settings.dry_run:
            print(f"[dry-run] Would create branch {branch} and run Codex across {repo_list}")
            return

        try:
            await self.linear.comment(issue.id, start_comment(issue, workspace, branch))
            plan = await self._plan(issue, workspace)
            await self.linear.comment(issue.id, plan_comment(plan))
            await self.linear.move_issue(issue.id, self.settings.in_progress_status)
            await self.linear.add_label(issue.id, self.settings.running_label)
            for repo in workspace.repos.values():
                ensure_branch(repo.path, repo.base, branch)

            await self._implement(issue, workspace, plan)
            changed_repos = self.changed_repos(workspace)
            await self.linear.comment(issue.id, implementation_comment(changed_repos))
            review = await self._review(issue, workspace, plan, changed_repos)
            await self.linear.comment(issue.id, review_comment(review))
        except Exception as exc:
            await self.linear.comment(issue.id, f"Codex orchestration failed:\n\n```text\n{exc}\n```")
            raise

        if not changed_repos:
            await self.linear.comment(issue.id, "Codex completed the run, but no git changes exist.")
            print(f"No changes for {issue.identifier}")
            return

        if not review.passed:
            await self.linear.comment(
                issue.id,
                f"Codex reviewer did not approve an automatic PR yet.\n\n{review.summary}",
            )
            print(f"Reviewer blocked {issue.identifier}")
            return

        prs: list[PullRequest] = []
        for repo_key, repo in changed_repos.items():
            commit_all(repo.path, f"{issue.identifier}: {issue.title}")
            if not self.settings.dry_run:
                push_branch(repo.path, branch)
            pr_body = pr_description(issue, repo_key, repo.path, plan, review)
            pr = await self.github.create_or_update_pr(
                repo.github,
                branch,
                repo.base,
                f"{issue.identifier}: {issue.title}",
                pr_body,
            )
            await self.linear.attach_pr(issue.id, pr.url)
            prs.append(pr)

        await self.linear.comment(issue.id, pr_links_comment(prs))
        await self.linear.move_issue(issue.id, self.settings.in_review_status)
        print(f"Opened/updated {len(prs)} PR(s) for {issue.identifier}")

    def changed_repos(self, workspace: WorkspaceConfig) -> dict[str, RepoConfig]:
        return {
            repo_key: repo
            for repo_key, repo in workspace.repos.items()
            if has_changes(repo.path)
        }

    async def _plan(self, issue: LinearIssue, workspace: WorkspaceConfig) -> str:
        plan = run_codex(
            planner_prompt(issue, workspace),
            workspace.path,
            model=self.settings.codex_model,
            sandbox="read-only",
            timeout_seconds=900,
        )
        if "BLOCKED" in plan.upper():
            await self.linear.comment(issue.id, f"Planner blocked automatic implementation.\n\n{plan}")
            raise RuntimeError(f"Planner blocked {issue.identifier}")
        return plan

    async def _implement(self, issue: LinearIssue, workspace: WorkspaceConfig, plan: str) -> None:
        run_codex(
            implementation_prompt(issue, workspace, plan),
            workspace.path,
            model=self.settings.codex_model,
            sandbox=self.settings.codex_sandbox,
        )

    async def _review(
        self,
        issue: LinearIssue,
        workspace: WorkspaceConfig,
        plan: str,
        changed_repos: dict[str, RepoConfig],
    ) -> ReviewResult:
        summary = run_codex(
            review_prompt(issue, workspace, plan, changed_repos, self.settings.test_command),
            workspace.path,
            model=self.settings.codex_model,
            sandbox="read-only",
            timeout_seconds=1800,
        )
        return ReviewResult(
            passed="REVIEW_DECISION: PASS" in summary,
            summary=summary,
            tests="See reviewer summary.",
        )


def issue_prompt(issue: LinearIssue, workspace: WorkspaceConfig) -> str:
    repos = "\n".join(
        f"- {repo_key}: {repo.github} at {repo.path} (base {repo.base})"
        for repo_key, repo in workspace.repos.items()
    )
    return f"""
Linear issue:
- ID: {issue.identifier}
- URL: {issue.url}
- Title: {issue.title}
- Team: {issue.team_key} ({issue.team_name})
- Workspace: {workspace.path}

Candidate repositories:
{repos}

Description:
{issue.description}
""".strip()


def planner_prompt(issue: LinearIssue, workspace: WorkspaceConfig) -> str:
    return f"""
You are the planner for an automated multi-repository software workflow.
Scope this Linear task before implementation. Decide which candidate repositories
are likely involved, summarize acceptance criteria, risks, and a compact plan.
If the task is vague, sensitive, or unsafe for automation, say BLOCKED clearly.

{issue_prompt(issue, workspace)}
""".strip()


def implementation_prompt(issue: LinearIssue, workspace: WorkspaceConfig, plan: str) -> str:
    return f"""
Implement this Linear issue in the workspace at {workspace.path}.

Issue: {issue.identifier} - {issue.title}
URL: {issue.url}

Planner scope:
{plan}

Requirements:
- Work across any candidate repositories needed to satisfy the issue.
- Make focused code changes only in the listed candidate repositories.
- Add or update tests when the change warrants it.
- Do not push or create pull requests.
- Do not move or comment on Linear issues.
- Leave each repo with only intentional changes.
""".strip()


def review_prompt(
    issue: LinearIssue,
    workspace: WorkspaceConfig,
    plan: str,
    changed_repos: dict[str, RepoConfig],
    test_command: str | None,
) -> str:
    changed = "\n".join(
        f"- {repo_key}: {repo.path}\n```text\n{changed_files(repo.path)}\n```"
        for repo_key, repo in changed_repos.items()
    ) or "- No changed repositories detected."
    test_instruction = (
        f'Run this test command from the workspace root and include the result: "{test_command}".'
        if test_command
        else "No TEST_COMMAND is configured; inspect diffs and run obvious lightweight checks if available."
    )
    return f"""
You are a strict read-only code reviewer. You may inspect files and run read-only
commands, but do not modify files.

Review the implementation for {issue.identifier}: {issue.title}.

Acceptance scope:
{plan}

Changed repositories:
{changed}

Check:
- git status and diff in each changed repo
- {test_instruction}
- whether the changes satisfy the issue without unrelated edits

End with exactly one line containing REVIEW_DECISION: PASS or REVIEW_DECISION: FAIL,
followed by a concise rationale.
""".strip()


def pr_description(
    issue: LinearIssue,
    repo_key: str,
    repo_path: Path,
    plan: str,
    review: ReviewResult,
) -> str:
    try:
        diffstat = run_git(repo_path, "diff", "--stat", "HEAD~1..HEAD")
    except Exception:
        diffstat = "Diffstat unavailable."
    return f"""
Linear issue: {issue.url}

Repository: {repo_key}

## Plan
{plan}

## Reviewer
{review.summary}

## Diffstat
```text
{diffstat}
```
""".strip()


def start_comment(issue: LinearIssue, workspace: WorkspaceConfig, branch: str) -> str:
    repos = "\n".join(
        f"- `{repo_key}`: `{repo.github}` from `{repo.base}`"
        for repo_key, repo in workspace.repos.items()
    )
    return f"""
Codex started work on `{issue.identifier}`.

Branch: `{branch}`
Workspace: `{workspace.path}`

Candidate repositories:
{repos}
""".strip()


def plan_comment(plan: str) -> str:
    return f"""
Codex plan:

{truncate_markdown(plan)}
""".strip()


def implementation_comment(changed_repos: dict[str, RepoConfig]) -> str:
    if not changed_repos:
        return "Codex implementation finished. No repository changes were detected."
    details = "\n\n".join(
        f"### `{repo_key}`\n\n```text\n{truncate_text(changed_files(repo.path), 3000)}\n```"
        for repo_key, repo in changed_repos.items()
    )
    return f"""
Codex implementation finished. Changed repositories:

{details}
""".strip()


def review_comment(review: ReviewResult) -> str:
    decision = "passed" if review.passed else "failed"
    return f"""
Codex reviewer {decision}.

{truncate_markdown(review.summary)}
""".strip()


def pr_links_comment(prs: list[PullRequest]) -> str:
    links = "\n".join(f"- {pr.url}" for pr in prs)
    return f"""
Draft PRs ready for review:

{links}
""".strip()


def truncate_markdown(value: str, limit: int = 6000) -> str:
    return truncate_text(value.strip(), limit)


def truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit].rstrip()}\n\n...[truncated]"
