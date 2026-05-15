from __future__ import annotations

import asyncio
import os
from pathlib import Path

from agents import Agent, Runner
from agents.mcp import MCPServerStdio

from .config import RepoConfig, Settings
from .git_ops import branch_name, commit_all, ensure_branch, has_changes, push_branch, run_git
from .github_client import GitHubClient
from .linear_client import LinearClient
from .locks import lock_for_repo
from .models import LinearIssue, ReviewResult
from .reviewer_tools import build_reviewer_tools


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        linear: LinearClient | None = None,
        github: GitHubClient | None = None,
    ) -> None:
        self.settings = settings
        self.linear = linear or LinearClient(settings.linear_api_key, dry_run=settings.dry_run)
        self.github = github or GitHubClient(settings.github_token, dry_run=settings.dry_run)

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
        repo = self.resolve_repo(issue)
        with lock_for_repo(self.settings.lock_dir, repo.github) as lock:
            if not lock.acquired:
                print(f"Skipping {issue.identifier}: repo lock is already held for {repo.github}")
                return
            await self._process_locked_issue(issue, repo)

    def resolve_repo(self, issue: LinearIssue) -> RepoConfig:
        labeled_matches = [
            repo for repo in self.settings.repo_map.values() if repo.label in issue.labels
        ]
        if len(labeled_matches) == 1:
            return labeled_matches[0]
        if len(labeled_matches) > 1:
            labels = ", ".join(repo.label or "" for repo in labeled_matches)
            raise RuntimeError(
                f"Multiple repository labels matched {issue.identifier}: {labels}"
            )
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
        agent = Agent(
            name="Planner",
            model=self.settings.agent_model,
            instructions=(
                "You scope Linear software tasks before implementation. "
                "Summarize acceptance criteria, likely files or areas, risks, and a compact plan. "
                "If the task is vague, sensitive, or unsafe for automation, say BLOCKED clearly."
            ),
        )
        prompt = issue_prompt(issue, repo)
        result = await Runner.run(agent, prompt)
        plan = str(result.final_output)
        if "BLOCKED" in plan.upper():
            await self.linear.comment(issue.id, f"Planner blocked automatic implementation.\n\n{plan}")
            raise RuntimeError(f"Planner blocked {issue.identifier}")
        return plan

    async def _implement(self, issue: LinearIssue, repo: RepoConfig, plan: str) -> None:
        env = os.environ.copy()
        env["OPENAI_API_KEY"] = self.settings.openai_api_key
        async with MCPServerStdio(
            name="codex",
            params={
                "command": "codex",
                "args": [
                    "mcp-server",
                    "-c",
                    f'model="{self.settings.codex_model}"',
                    "-c",
                    f'sandbox_mode="{self.settings.codex_sandbox}"',
                ],
                "env": env,
            },
            cache_tools_list=True,
            require_approval="never",
        ) as codex_server:
            agent = Agent(
                name="Implementer",
                model=self.settings.agent_model,
                instructions=(
                    "You implement scoped software changes by using the Codex MCP tools. "
                    "Work only in the target repository and branch. "
                    "Do not push, create PRs, or move Linear issues. "
                    "Stop after implementation and report changed behavior."
                ),
                mcp_servers=[codex_server],
                mcp_config={"include_server_in_tool_names": True},
            )
            await Runner.run(agent, implementation_prompt(issue, repo.path, plan))

    async def _review(self, issue: LinearIssue, repo: RepoConfig, plan: str) -> ReviewResult:
        agent = Agent(
            name="Reviewer",
            model=self.settings.agent_model,
            instructions=(
                "You are a strict read-only code reviewer. Use tools to inspect git status, "
                "diff, and tests. You cannot modify files. End with exactly one line containing "
                "REVIEW_DECISION: PASS or REVIEW_DECISION: FAIL, followed by a concise rationale."
            ),
            tools=build_reviewer_tools(repo.path, self.settings.test_command),
        )
        result = await Runner.run(agent, review_prompt(issue, plan))
        summary = str(result.final_output)
        tests = "See reviewer transcript in Agents SDK trace; final summary captured here."
        return ReviewResult(
            passed="REVIEW_DECISION: PASS" in summary,
            summary=summary,
            tests=tests,
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
- Leave a clean working tree except for intentional changes.
""".strip()


def review_prompt(issue: LinearIssue, plan: str) -> str:
    return f"""
Review the implementation for {issue.identifier}: {issue.title}.

Acceptance scope:
{plan}

Check:
- git status
- diff against HEAD
- configured tests
- whether the changes satisfy the issue without unrelated edits
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
