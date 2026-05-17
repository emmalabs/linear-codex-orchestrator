from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from .codex_cli import run_codex
from .config import RepoConfig, Settings, WorkspaceConfig
from .git_ops import (
    branch_name,
    changed_files,
    checkout_branch,
    commit_all,
    ensure_branch,
    has_changes,
    has_commits_since_base,
    push_branch,
    run_git,
)
from .local_github_client import LocalGitHubClient
from .local_linear_client import LocalLinearClient
from .locks import lock_for_repo
from .models import LinearIssue, OpenPullRequest, PullRequest, PullRequestFeedback, ReviewResult
from .prompt_templates import render_prompt


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
            reasoning_effort=settings.codex_reasoning_effort,
            fast_mode=settings.codex_fast_mode,
        )
        self.github = github or LocalGitHubClient(dry_run=settings.dry_run)

    async def close(self) -> None:
        await self.linear.close()
        await self.github.close()

    async def run_once(self) -> None:
        await self.run_pr_feedback_once()
        log("Polling Linear for resumable running issues")
        running_issues = await self.linear.ready_issues(
            self.settings.in_progress_status,
            self.settings.running_label,
            self.settings.max_issues_per_tick,
            (self.settings.blocked_label,),
            tuple(sorted(self.settings.workspace_map)),
        )
        log(f"Found {len(running_issues)} resumable issue(s)")
        for issue in running_issues:
            try:
                await self.process_issue(issue, resume=True)
            except Exception as exc:
                log(f"{issue.identifier}: issue resume failed; daemon will continue: {exc}")
        if running_issues:
            return

        log("Polling Linear for ready issues")
        issues = await self.linear.ready_issues(
            self.settings.todo_status,
            self.settings.ready_label,
            self.settings.max_issues_per_tick,
            (self.settings.running_label, self.settings.blocked_label),
            tuple(sorted(self.settings.workspace_map)),
        )
        log(f"Found {len(issues)} ready issue(s)")
        for issue in issues:
            try:
                await self.process_issue(issue, resume=False)
            except Exception as exc:
                log(f"{issue.identifier}: issue processing failed; daemon will continue: {exc}")

    async def run_forever(self, interval_seconds: int = 60) -> None:
        log_session_start()
        log(f"Daemon started; polling every {interval_seconds}s after each completed tick")
        while True:
            try:
                await self.run_once()
            except Exception as exc:
                log(f"Tick failed; daemon will continue: {exc}")
            log(f"Tick complete; sleeping {interval_seconds}s")
            await asyncio.sleep(interval_seconds)

    async def run_pr_feedback_once(self) -> None:
        log("Checking open PRs for new feedback")
        for workspace in self.settings.workspace_map.values():
            for repo_key, repo in workspace.repos.items():
                prs = await self.github.list_open_prs(
                    repo.github,
                    branch_prefix=self.settings.pr_feedback_branch_prefix,
                )
                log(f"{repo_key}: found {len(prs)} open PR(s) for feedback check")
                for pr in prs:
                    try:
                        await self.process_pr_feedback(repo_key, repo, pr)
                    except Exception as exc:
                        log(f"{repo.github}#{pr.number}: PR feedback processing failed; daemon will continue: {exc}")
        log("PR feedback check complete")

    async def process_pr_feedback(self, repo_key: str, repo: RepoConfig, pr: OpenPullRequest) -> None:
        lock_name = f"pr-feedback:{repo.github}:{pr.number}"
        with lock_for_repo(self.settings.lock_dir, lock_name) as lock:
            if not lock.acquired:
                log(f"Skipping {repo.github}#{pr.number}: PR feedback lock is already held")
                return
            feedback = await self.github.pr_feedback(repo.github, pr.number)
            state = pr_feedback_state(self.settings.lock_dir, repo.github, pr.number)
            seen = read_processed_feedback(state)
            new_feedback = [item for item in feedback if item.key not in seen]
            if not new_feedback:
                log(f"{repo.github}#{pr.number}: no new PR feedback")
                update_pr_status(pr, "No new feedback", repo_key=repo_key, repo_path=repo.path, feedback_count=0)
                return
            log(f"{repo.github}#{pr.number}: found {len(new_feedback)} new feedback item(s)")
            update_pr_status(pr, "Feedback found", repo_key=repo_key, repo_path=repo.path, feedback_count=len(new_feedback))
            if self.settings.dry_run:
                log(f"[dry-run] Would address PR feedback on {repo.github}#{pr.number}")
                return

            run_git(repo.path, "fetch", "origin", pr.head_branch)
            run_git(repo.path, "checkout", "-B", pr.head_branch, f"origin/{pr.head_branch}")
            update_pr_status(pr, "Fixing feedback", repo_key=repo_key, repo_path=repo.path, feedback_count=len(new_feedback))
            summary = await self._fix_pr_feedback(repo_key, repo, pr, new_feedback)
            if has_changes(repo.path):
                log(f"{repo.github}#{pr.number}: committing PR feedback fixes")
                commit_all(repo.path, f"Address PR feedback for #{pr.number}")
                log(f"{repo.github}#{pr.number}: pushing PR feedback fixes")
                push_branch(repo.path, pr.head_branch)
                await self.github.comment_on_pr(repo.github, pr.number, pr_feedback_comment(summary))
                update_pr_status(pr, "Feedback addressed", repo_key=repo_key, repo_path=repo.path, feedback_count=len(new_feedback))
            else:
                log(f"{repo.github}#{pr.number}: no changes after PR feedback pass")
                await self.github.comment_on_pr(
                    repo.github,
                    pr.number,
                    pr_feedback_no_changes_comment(summary),
                )
                update_pr_status(pr, "Checked feedback", repo_key=repo_key, repo_path=repo.path, feedback_count=len(new_feedback))
            write_processed_feedback(state, seen | {item.key for item in new_feedback})

    async def process_issue(self, issue: LinearIssue, resume: bool = False) -> None:
        try:
            workspace = self.resolve_workspace(issue)
        except RuntimeError as exc:
            log(f"Skipping {issue.identifier}: {exc}")
            return
        lock_name = f"{issue.team_key}:{workspace.path}"
        with lock_for_repo(self.settings.lock_dir, lock_name) as lock:
            if not lock.acquired:
                log(f"Skipping {issue.identifier}: workspace lock is already held")
                return
            await self._process_locked_issue(issue, workspace, resume)

    def resolve_workspace(self, issue: LinearIssue) -> WorkspaceConfig:
        normalized_key = issue.team_key.upper()
        try:
            return self.settings.workspace_map[normalized_key]
        except KeyError as exc:
            raise RuntimeError(
                f"No WORKSPACE_MAP_JSON entry for Linear team key {issue.team_key}."
            ) from exc

    async def _process_locked_issue(self, issue: LinearIssue, workspace: WorkspaceConfig, resume: bool = False) -> None:
        branch = branch_name(issue.identifier, issue.title)
        repo_list = ", ".join(workspace.repos)
        mode = "resuming" if resume else "processing"
        log(f"{mode.capitalize()} {issue.identifier} in {workspace.path} across: {repo_list}")
        update_issue_status(issue, "Resuming" if resume else "Starting", **workspace_status_context(workspace))
        if self.settings.dry_run:
            action = "resume branch" if resume else "create branch"
            log(f"[dry-run] Would {action} {branch} and run Codex across {repo_list}")
            update_issue_status(issue, "Dry run")
            return

        try:
            log(f"{issue.identifier}: reading full Linear issue context")
            issue_context = await self.linear.issue_context(issue)
            if resume:
                log(f"{issue.identifier}: resuming existing branch {branch}")
                update_issue_status(issue, "Resuming branch")
                self.checkout_existing_branch(workspace, branch)
                plan = "Resume interrupted automation from the existing branch and working tree."
                implementation_summary = "Resumed from existing repository changes after an interrupted previous run."
                changed_repos = self.changed_repos(workspace)
                log(f"{issue.identifier}: changed repos on resume: {', '.join(changed_repos) or 'none'}")
                update_issue_status(issue, "Resumed", changed_repos=", ".join(changed_repos) or "none")
            else:
                log(f"{issue.identifier}: posting start comment")
                await self.linear.comment(issue.id, start_comment(issue, workspace, branch))
                log(f"{issue.identifier}: planning")
                update_issue_status(issue, "Planning")
                plan = await self._plan(issue, workspace, issue_context)
                log(f"{issue.identifier}: posting plan and moving to {self.settings.in_progress_status}")
                await self.linear.comment(issue.id, plan_comment(plan))
                await self.linear.move_issue(issue.id, self.settings.in_progress_status)
                await self.linear.add_label(issue.id, self.settings.running_label)
                log(f"{issue.identifier}: preparing branch {branch} in {len(workspace.repos)} repo(s)")
                update_issue_status(issue, "Preparing branches")
                for repo_key, repo in workspace.repos.items():
                    log(f"{issue.identifier}: ensuring {repo_key} branch {branch}")
                    ensure_branch(repo.path, repo.base, branch)

                log(f"{issue.identifier}: implementation started")
                update_issue_status(issue, "Implementing")
                implementation_summary = await self._implement(issue, workspace, issue_context, plan)
                log(f"{issue.identifier}: implementation finished; detecting changed repos")
                changed_repos = self.changed_repos(workspace)
                log(f"{issue.identifier}: changed repos: {', '.join(changed_repos) or 'none'}")
                update_issue_status(issue, "Implemented", changed_repos=", ".join(changed_repos) or "none")
                await self._try_linear_comment(issue, implementation_comment(changed_repos, implementation_summary))
            if changed_repos:
                log(f"{issue.identifier}: optimization started")
                update_issue_status(issue, "Optimizing", changed_repos=", ".join(changed_repos))
                optimization_summary = await self._optimize(
                    issue,
                    workspace,
                    issue_context,
                    plan,
                    changed_repos,
                    implementation_summary,
                )
                log(f"{issue.identifier}: optimization finished; detecting changed repos")
                changed_repos = self.changed_repos(workspace)
                update_issue_status(issue, "Optimized", changed_repos=", ".join(changed_repos) or "none")
                await self._try_linear_comment(issue, optimization_comment(optimization_summary))
            log(f"{issue.identifier}: review started")
            update_issue_status(issue, "Reviewing", changed_repos=", ".join(changed_repos) or "none")
            review = await self._review(issue, workspace, issue_context, plan, changed_repos)
            log(f"{issue.identifier}: review {'passed' if review.passed else 'failed'}")
            update_issue_status(issue, "Review passed" if review.passed else "Review failed")
            await self._try_linear_comment(issue, review_comment(review))
            if changed_repos and not review.passed:
                log(f"{issue.identifier}: reviewer-fix pass started")
                update_issue_status(issue, "Fixing review findings")
                fix_summary = await self._fix_review_findings(
                    issue,
                    workspace,
                    issue_context,
                    plan,
                    changed_repos,
                    review,
                )
                log(f"{issue.identifier}: reviewer-fix pass finished; re-review started")
                changed_repos = self.changed_repos(workspace)
                await self._try_linear_comment(issue, review_fix_comment(fix_summary))
                review = await self._review(issue, workspace, issue_context, plan, changed_repos)
                log(f"{issue.identifier}: re-review {'passed' if review.passed else 'failed'}")
                update_issue_status(issue, "Re-review passed" if review.passed else "Re-review failed")
                await self._try_linear_comment(issue, review_comment(review))
        except PlannerBlocked as exc:
            log(
                f"{issue.identifier}: planner blocked; reason: "
                f"{planner_block_reason(exc.plan)}; adding {self.settings.blocked_label} label"
            )
            await self._try_linear_comment(issue, planner_blocked_comment(exc.plan))
            await self._try_linear_action(
                issue,
                f"add {self.settings.blocked_label} label",
                self.linear.add_label(issue.id, self.settings.blocked_label),
            )
            await self._clear_running_label(issue)
            update_issue_status(issue, "Blocked")
            return
        except Exception as exc:
            log(f"{issue.identifier}: failed: {exc}")
            update_issue_status(issue, "Failed", error=str(exc))
            await self._try_linear_comment(issue, f"Codex orchestration failed:\n\n```text\n{exc}\n```")
            await self._clear_running_label(issue)
            raise

        if not changed_repos:
            await self._try_linear_comment(issue, "Codex completed the run, but no git changes exist.")
            await self._clear_running_label(issue)
            log(f"{issue.identifier}: no changes detected; stopping")
            update_issue_status(issue, "No changes")
            return

        if not review.passed:
            await self._try_linear_comment(
                issue,
                f"Codex reviewer did not approve an automatic PR yet.\n\n{review.summary}",
            )
            await self._clear_running_label(issue)
            log(f"{issue.identifier}: reviewer blocked automatic PR")
            update_issue_status(issue, "Reviewer blocked PR")
            return

        prs: list[PullRequest] = []
        for repo_key, repo in changed_repos.items():
            if has_changes(repo.path):
                log(f"{issue.identifier}: committing changes in {repo_key}")
                commit_all(repo.path, f"{issue.identifier}: {issue.title}")
            else:
                log(f"{issue.identifier}: no uncommitted changes in {repo_key}; using existing branch commits")
            if not self.settings.dry_run:
                log(f"{issue.identifier}: pushing {repo_key} branch {branch}")
                push_branch(repo.path, branch)
            pr_body = pr_description(issue, repo_key, repo.path, plan, review)
            log(f"{issue.identifier}: creating/updating ready-for-review PR for {repo_key}")
            pr = await self.github.create_or_update_pr(
                repo.github,
                branch,
                repo.base,
                f"{issue.identifier}: {issue.title}",
                pr_body,
            )
            log(f"{issue.identifier}: attaching PR to Linear: {pr.url}")
            update_pr_status(
                OpenPullRequest(repo.github, pr.number, pr.url, pr.title, branch, repo.base),
                "Ready for review",
                issue=issue.identifier,
                repo_key=repo_key,
                repo_path=repo.path,
            )
            await self._try_linear_action(
                issue,
                f"attach PR to Linear: {pr.url}",
                self.linear.attach_pr(issue.id, pr.url),
            )
            prs.append(pr)

        log(f"{issue.identifier}: posting ready-for-review PR links and moving to {self.settings.in_review_status}")
        await self._try_linear_comment(issue, pr_links_comment(prs))
        await self._try_linear_action(
            issue,
            f"move to {self.settings.in_review_status}",
            self.linear.move_issue(issue.id, self.settings.in_review_status),
        )
        await self._clear_running_label(issue)
        log(f"{issue.identifier}: opened/updated {len(prs)} PR(s)")
        update_issue_status(issue, "PR ready", prs=", ".join(pr.url for pr in prs))

    async def _try_linear_comment(self, issue: LinearIssue, body: str) -> bool:
        return await self._try_linear_action(issue, "post Linear comment", self.linear.comment(issue.id, body))

    async def _try_linear_action(self, issue: LinearIssue, description: str, action: object) -> bool:
        try:
            await action
            return True
        except Exception as exc:
            log(f"{issue.identifier}: failed to {description}: {exc}")
            return False

    async def _clear_running_label(self, issue: LinearIssue) -> None:
        await self._try_linear_action(
            issue,
            f"remove {self.settings.running_label} label",
            self.linear.remove_label(issue.id, self.settings.running_label),
        )

    def changed_repos(self, workspace: WorkspaceConfig) -> dict[str, RepoConfig]:
        return {
            repo_key: repo
            for repo_key, repo in workspace.repos.items()
            if has_changes(repo.path) or has_commits_since_base(repo.path, repo.base)
        }

    def checkout_existing_branch(self, workspace: WorkspaceConfig, branch: str) -> None:
        missing: list[str] = []
        for repo_key, repo in workspace.repos.items():
            if not checkout_branch(repo.path, branch):
                missing.append(repo_key)
        if missing:
            raise RuntimeError(f"Cannot resume because branch {branch} is missing in: {', '.join(missing)}")

    async def _plan(self, issue: LinearIssue, workspace: WorkspaceConfig, issue_context: str) -> str:
        log_path = codex_log_path(issue.identifier, "planner")
        log(f"{issue.identifier}: planner output: {log_path}")
        plan = run_codex(
            planner_prompt(issue, workspace, issue_context),
            workspace.path,
            model=self.settings.codex_model,
            reasoning_effort=self.settings.codex_reasoning_effort,
            fast_mode=self.settings.codex_fast_mode,
            sandbox="read-only",
            timeout_seconds=900,
            log_output_path=log_path,
        )
        if planner_is_blocked(plan):
            raise PlannerBlocked(plan)
        return plan

    async def _implement(
        self,
        issue: LinearIssue,
        workspace: WorkspaceConfig,
        issue_context: str,
        plan: str,
    ) -> str:
        log_path = codex_log_path(issue.identifier, "implementation")
        log(f"{issue.identifier}: implementation output: {log_path}")
        return run_codex(
            implementation_prompt(issue, workspace, issue_context, plan),
            workspace.path,
            model=self.settings.codex_model,
            reasoning_effort=self.settings.codex_reasoning_effort,
            fast_mode=self.settings.codex_fast_mode,
            sandbox=self.settings.codex_sandbox,
            log_output_path=log_path,
        )

    async def _review(
        self,
        issue: LinearIssue,
        workspace: WorkspaceConfig,
        issue_context: str,
        plan: str,
        changed_repos: dict[str, RepoConfig],
    ) -> ReviewResult:
        log_path = codex_log_path(issue.identifier, "review")
        log(f"{issue.identifier}: review output: {log_path}")
        summary = run_codex(
            review_prompt(issue, workspace, issue_context, plan, changed_repos, self.settings.test_command),
            workspace.path,
            model=self.settings.codex_model,
            reasoning_effort=self.settings.codex_reasoning_effort,
            fast_mode=self.settings.codex_fast_mode,
            sandbox="read-only",
            timeout_seconds=1800,
            log_output_path=log_path,
        )
        return ReviewResult(
            passed="REVIEW_DECISION: PASS" in summary,
            summary=summary,
            tests="See reviewer summary.",
        )

    async def _optimize(
        self,
        issue: LinearIssue,
        workspace: WorkspaceConfig,
        issue_context: str,
        plan: str,
        changed_repos: dict[str, RepoConfig],
        implementation_summary: str,
    ) -> str:
        log_path = codex_log_path(issue.identifier, "optimization")
        log(f"{issue.identifier}: optimization output: {log_path}")
        return run_codex(
            optimizer_prompt(issue, workspace, issue_context, plan, changed_repos, implementation_summary),
            workspace.path,
            model=self.settings.codex_model,
            reasoning_effort=self.settings.codex_reasoning_effort,
            fast_mode=self.settings.codex_fast_mode,
            sandbox=self.settings.codex_sandbox,
            log_output_path=log_path,
        )

    async def _fix_review_findings(
        self,
        issue: LinearIssue,
        workspace: WorkspaceConfig,
        issue_context: str,
        plan: str,
        changed_repos: dict[str, RepoConfig],
        review: ReviewResult,
    ) -> str:
        log_path = codex_log_path(issue.identifier, "review-fix")
        log(f"{issue.identifier}: review-fix output: {log_path}")
        return run_codex(
            review_fix_prompt(issue, workspace, issue_context, plan, changed_repos, review.summary),
            workspace.path,
            model=self.settings.codex_model,
            reasoning_effort=self.settings.codex_reasoning_effort,
            fast_mode=self.settings.codex_fast_mode,
            sandbox=self.settings.codex_sandbox,
            log_output_path=log_path,
        )

    async def _fix_pr_feedback(
        self,
        repo_key: str,
        repo: RepoConfig,
        pr: OpenPullRequest,
        feedback: list[PullRequestFeedback],
    ) -> str:
        log_path = codex_log_path(f"{repo_key}-{pr.number}", "pr-feedback")
        log(f"{repo_key}#{pr.number}: PR feedback output: {log_path}")
        return run_codex(
            pr_feedback_prompt(repo_key, repo, pr, feedback),
            repo.path,
            model=self.settings.codex_model,
            reasoning_effort=self.settings.codex_reasoning_effort,
            fast_mode=self.settings.codex_fast_mode,
            sandbox=self.settings.codex_sandbox,
            log_output_path=log_path,
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


def planner_prompt(issue: LinearIssue, workspace: WorkspaceConfig, issue_context: str) -> str:
    return render_prompt(
        "planner.md",
        issue_identifier=issue.identifier,
        issue_context=issue_prompt(issue, workspace),
        full_issue_context=issue_context,
    )


def implementation_prompt(issue: LinearIssue, workspace: WorkspaceConfig, issue_context: str, plan: str) -> str:
    return render_prompt(
        "implementation.md",
        workspace_path=workspace.path,
        issue_identifier=issue.identifier,
        issue_title=issue.title,
        issue_url=issue.url,
        issue_context=issue_context,
        plan=plan,
    )


def review_prompt(
    issue: LinearIssue,
    workspace: WorkspaceConfig,
    issue_context: str,
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
    return render_prompt(
        "reviewer.md",
        issue_identifier=issue.identifier,
        issue_title=issue.title,
        issue_context=issue_context,
        plan=plan,
        changed_repos=changed,
        test_instruction=test_instruction,
    )


def optimizer_prompt(
    issue: LinearIssue,
    workspace: WorkspaceConfig,
    issue_context: str,
    plan: str,
    changed_repos: dict[str, RepoConfig],
    implementation_summary: str,
) -> str:
    changed = "\n".join(
        f"- {repo_key}: {repo.path}\n```text\n{changed_files(repo.path)}\n```"
        for repo_key, repo in changed_repos.items()
    ) or "- No changed repositories detected."
    return render_prompt(
        "optimizer.md",
        workspace_path=workspace.path,
        issue_identifier=issue.identifier,
        issue_title=issue.title,
        issue_url=issue.url,
        issue_context=issue_context,
        plan=plan,
        changed_repos=changed,
        implementation_summary=implementation_summary,
    )


def review_fix_prompt(
    issue: LinearIssue,
    workspace: WorkspaceConfig,
    issue_context: str,
    plan: str,
    changed_repos: dict[str, RepoConfig],
    review_summary: str,
) -> str:
    changed = "\n".join(
        f"- {repo_key}: {repo.path}\n```text\n{changed_files(repo.path)}\n```"
        for repo_key, repo in changed_repos.items()
    ) or "- No changed repositories detected."
    return render_prompt(
        "review_fix.md",
        workspace_path=workspace.path,
        issue_identifier=issue.identifier,
        issue_title=issue.title,
        issue_url=issue.url,
        issue_context=issue_context,
        plan=plan,
        changed_repos=changed,
        review_summary=review_summary,
    )


def pr_feedback_prompt(
    repo_key: str,
    repo: RepoConfig,
    pr: OpenPullRequest,
    feedback: list[PullRequestFeedback],
) -> str:
    feedback_text = "\n\n".join(
        pr_feedback_item_text(item)
        for item in feedback
    )
    return render_prompt(
        "pr_feedback_fix.md",
        repo_key=repo_key,
        repo_path=repo.path,
        repo_github=repo.github,
        pr_number=pr.number,
        pr_title=pr.title,
        pr_url=pr.url,
        head_branch=pr.head_branch,
        base_branch=pr.base_branch,
        feedback=feedback_text,
    )


def pr_feedback_item_text(item: PullRequestFeedback) -> str:
    path = f"\nPath: `{item.path}`" if item.path else ""
    return f"""
### {item.kind} by {item.author}

URL: {item.url}{path}

```text
{item.body.strip()}
```
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
        f"- `{repo_key}`: `{repo.github}` from `{repo.base}` at `{repo.path}`"
        for repo_key, repo in workspace.repos.items()
    )
    return f"""
Codex started work on `{issue.identifier}`.

Branch: `{branch}`

Candidate repositories:
{repos}
""".strip()


def plan_comment(plan: str) -> str:
    return f"""
Codex plan:

{truncate_markdown(plan)}
""".strip()


def planner_blocked_comment(plan: str) -> str:
    return f"""
Planner blocked automatic implementation.

Reason: {planner_block_reason(plan)}

Next action: update the Linear issue with the missing context, then remove the `agent-blocked` label to retry.

{truncate_markdown(plan)}
""".strip()


def implementation_comment(changed_repos: dict[str, RepoConfig], summary: str = "") -> str:
    context = (
        f"\n\nImplementation context:\n\n{truncate_markdown(summary, 6000)}"
        if summary.strip()
        else ""
    )
    if not changed_repos:
        return f"Codex implementation finished. No repository changes were detected.{context}"
    details = "\n\n".join(
        f"### `{repo_key}`\n\n```text\n{truncate_text(changed_files(repo.path), 3000)}\n```"
        for repo_key, repo in changed_repos.items()
    )
    return f"""
Codex implementation finished. Changed repositories:

{details}
{context}
""".strip()


def review_comment(review: ReviewResult) -> str:
    decision = "passed" if review.passed else "failed"
    return f"""
Codex reviewer {decision}.

{truncate_markdown(review.summary)}
""".strip()


def review_fix_comment(summary: str) -> str:
    return f"""
Codex addressed reviewer findings.

{truncate_markdown(summary)}
""".strip()


def optimization_comment(summary: str) -> str:
    return f"""
Codex optimization pass finished.

{truncate_markdown(summary)}
""".strip()


def pr_links_comment(prs: list[PullRequest]) -> str:
    links = "\n".join(f"- {pr.url}" for pr in prs)
    return f"""
PRs ready for review:

{links}
""".strip()


def pr_feedback_comment(summary: str) -> str:
    return f"""
Codex addressed new PR feedback.

{truncate_markdown(summary)}
""".strip()


def pr_feedback_no_changes_comment(summary: str) -> str:
    return f"""
Codex checked the new PR feedback and did not make repository changes.

{truncate_markdown(summary)}
""".strip()


def truncate_markdown(value: str, limit: int = 6000) -> str:
    return truncate_text(value.strip(), limit)


def truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit].rstrip()}\n\n...[truncated]"


def planner_is_blocked(plan: str) -> bool:
    return any(line.strip().upper().startswith("BLOCKED") for line in plan.splitlines())


def planner_block_reason(plan: str) -> str:
    for line in plan.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("BLOCKED"):
            reason = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            return reason or "Planner marked the issue as blocked without a specific reason."
    first_line = next((line.strip() for line in plan.splitlines() if line.strip()), "")
    return first_line or "Planner did not provide a reason."


def log(message: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    orchestration_log_path().parent.mkdir(parents=True, exist_ok=True)
    with orchestration_log_path().open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")


def log_session_start() -> None:
    marker = f"===== New session started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ====="
    print(marker, flush=True)
    orchestration_log_path().parent.mkdir(parents=True, exist_ok=True)
    with orchestration_log_path().open("a", encoding="utf-8") as handle:
        handle.write(f"\n{marker}\n")


def orchestration_log_path() -> Path:
    return Path(".logs") / "orchestrator.log"


def status_path() -> Path:
    return Path(".logs") / "status.json"


def read_status() -> dict[str, object]:
    try:
        with status_path().open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        payload = {}
    return {
        "issues": payload.get("issues", {}) if isinstance(payload.get("issues", {}), dict) else {},
        "prs": payload.get("prs", {}) if isinstance(payload.get("prs", {}), dict) else {},
    }


def write_status(payload: dict[str, object]) -> None:
    status_path().parent.mkdir(parents=True, exist_ok=True)
    with status_path().open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def update_issue_status(issue: LinearIssue, status: str, **extra: object) -> None:
    payload = read_status()
    issues = payload["issues"]
    assert isinstance(issues, dict)
    current = issues.get(issue.identifier, {})
    if not isinstance(current, dict):
        current = {}
    current.update(
        {
            "identifier": issue.identifier,
            "title": issue.title,
            "url": issue.url,
            "team": issue.team_key,
            "project": issue.project_name,
            "project_url": issue.project_url,
            "status": status,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    current.update({key: value for key, value in extra.items() if value is not None})
    issues[issue.identifier] = current
    write_status(payload)


def update_pr_status(
    pr: OpenPullRequest,
    status: str,
    *,
    issue: str | None = None,
    repo_key: str | None = None,
    repo_path: Path | None = None,
    feedback_count: int | None = None,
) -> None:
    payload = read_status()
    prs = payload["prs"]
    assert isinstance(prs, dict)
    key = f"{pr.repo}#{pr.number}"
    current = prs.get(key, {})
    if not isinstance(current, dict):
        current = {}
    current.update(
        {
            "key": key,
            "repo": pr.repo,
            "number": pr.number,
            "title": pr.title,
            "url": pr.url,
            "branch": pr.head_branch,
            "base": pr.base_branch,
            "status": status,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    if issue:
        current["issue"] = issue
    if repo_key:
        current["repo_key"] = repo_key
    if repo_path:
        current["repo_path"] = str(repo_path)
    if feedback_count is not None:
        current["feedback_count"] = feedback_count
    prs[key] = current
    write_status(payload)


def workspace_status_context(workspace: WorkspaceConfig) -> dict[str, object]:
    return {
        "workspace_path": str(workspace.path),
        "repos": [
            {
                "key": repo_key,
                "github": repo.github,
                "path": str(repo.path),
                "base": repo.base,
            }
            for repo_key, repo in workspace.repos.items()
        ],
    }


def codex_log_path(identifier: str, stage: str) -> Path:
    safe_identifier = re.sub(r"[^a-zA-Z0-9_.-]+", "-", identifier).strip("-").lower()
    safe_stage = re.sub(r"[^a-zA-Z0-9_.-]+", "-", stage).strip("-").lower()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(".logs") / f"{timestamp}-{safe_identifier}-{safe_stage}.log"


def pr_feedback_state(lock_dir: Path, repo: str, number: int) -> Path:
    safe_repo = repo.replace("/", "__")
    return lock_dir / "pr-feedback-state" / f"{safe_repo}-{number}.json"


def read_processed_feedback(path: Path) -> set[str]:
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return set()
    except json.JSONDecodeError:
        return set()
    return set(payload.get("processed", []))


def write_processed_feedback(path: Path, processed: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump({"processed": sorted(processed)}, handle, indent=2)
        handle.write("\n")


class PlannerBlocked(Exception):
    def __init__(self, plan: str) -> None:
        super().__init__("Planner blocked automatic implementation")
        self.plan = plan
