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
    branch_exists,
    changed_files,
    checkout_branch,
    commit_all,
    ensure_branch,
    has_changes,
    has_commits_since_base,
    push_branch,
    remote_branch_exists,
    run_git,
)
from .local_github_client import LocalGitHubClient
from .local_linear_client import LocalLinearClient
from .linear_api_client import LinearApiClient
from .locks import lock_for_repo
from .models import (
    LinearCommentFeedback,
    LinearIssue,
    OpenPullRequest,
    PullRequest,
    PullRequestApproval,
    PullRequestFeedback,
    ReviewResult,
    is_orchestrator_linear_comment,
    mark_linear_orchestrator_comment,
)
from .prompt_templates import render_prompt
from .run_state import clear_issue_run_state, read_issue_run_state, write_issue_run_state


STAGES_AFTER_IMPLEMENTATION = {
    "implemented",
    "optimizing",
    "optimized",
    "reviewing",
    "review_fixing",
    "review_fixed",
    "pr_creating",
}
STAGES_AFTER_OPTIMIZATION = {"optimized", "reviewing", "review_fixing", "review_fixed", "pr_creating"}


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        linear: object | None = None,
        github: LocalGitHubClient | None = None,
    ) -> None:
        self.settings = settings
        self.linear = linear or self._linear_client()
        self.github = github or LocalGitHubClient(dry_run=settings.dry_run)

    def _linear_client(self) -> object:
        if self.settings.linear_api_key:
            log("Linear backend: direct API")
            return LinearApiClient(self.settings.linear_api_key, dry_run=self.settings.dry_run)
        log("Linear backend: Codex MCP fallback")
        return LocalLinearClient(
            Path.cwd(),
            dry_run=self.settings.dry_run,
            model=self.settings.codex_model,
            reasoning_effort=self.settings.codex_reasoning_effort,
            fast_mode=self.settings.codex_fast_mode,
        )

    async def close(self) -> None:
        await self.linear.close()
        await self.github.close()

    async def reload_settings(self) -> None:
        next_settings = Settings.from_env()
        if next_settings == self.settings:
            return
        old_settings = self.settings
        self.settings = next_settings
        if linear_client_settings_changed(old_settings, next_settings):
            await self.linear.close()
            self.linear = self._linear_client()
        if github_client_settings_changed(old_settings, next_settings):
            await self.github.close()
            self.github = LocalGitHubClient(dry_run=next_settings.dry_run)
        log("Config hot-reloaded; changes are active for the next tick")

    async def run_once(self) -> None:
        await self.run_pr_feedback_once()
        await self.run_linear_feedback_once()
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

        log("Polling Linear for interrupted in-progress issues with existing Codex branches")
        interrupted_issues = await self.interrupted_issues()
        log(f"Found {len(interrupted_issues)} interrupted issue(s)")
        for issue in interrupted_issues:
            try:
                await self.process_issue(issue, resume=True)
            except Exception as exc:
                log(f"{issue.identifier}: interrupted issue resume failed; daemon will continue: {exc}")
        if interrupted_issues:
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
                if self.settings.hot_reload_config:
                    await self.reload_settings()
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
                await self.archive_merged_prs(repo.github, repo_key)
                log(f"{repo_key}: found {len(prs)} open PR(s) for feedback check")
                await archive_stale_prs(self.github, repo.github, prs)
                for pr in prs:
                    try:
                        await self.process_pr_feedback(repo_key, repo, pr)
                    except Exception as exc:
                        log(f"{repo.github}#{pr.number}: PR feedback processing failed; daemon will continue: {exc}")
        log("PR feedback check complete")

    async def run_linear_feedback_once(self) -> None:
        log("Checking Linear issue comments for new feedback")
        candidates = await self.linear_feedback_candidates()
        log(f"Found {len(candidates)} Linear issue feedback candidate(s)")
        processed = 0
        for issue in candidates:
            if processed >= self.settings.max_issues_per_tick:
                break
            try:
                if await self.process_linear_feedback(issue):
                    processed += 1
            except Exception as exc:
                log(f"{issue.identifier}: Linear feedback processing failed; daemon will continue: {exc}")
        log("Linear feedback check complete")

    async def linear_feedback_candidates(self) -> list[LinearIssue]:
        return await self.linear.ready_issues(
            self.settings.in_review_status,
            None,
            max(self.settings.max_issues_per_tick * 10, 10),
            (self.settings.running_label, self.settings.blocked_label),
            tuple(sorted(self.settings.workspace_map)),
        )

    async def archive_merged_prs(self, repo: str, repo_key: str) -> None:
        list_merged_prs = getattr(self.github, "list_merged_prs", None)
        if not callable(list_merged_prs):
            return
        try:
            merged_prs = await list_merged_prs(
                repo,
                branch_prefix=self.settings.pr_feedback_branch_prefix,
            )
        except Exception as exc:
            log(f"{repo_key}: merged PR archive check failed; daemon will continue: {exc}")
            return
        archived = 0
        for pr in merged_prs:
            if archive_pr_status(pr):
                archived += 1
        if archived:
            log(f"{repo_key}: archived {archived} merged PR status entr{'y' if archived == 1 else 'ies'}")

    async def process_pr_feedback(self, repo_key: str, repo: RepoConfig, pr: OpenPullRequest) -> None:
        lock_name = f"pr-feedback:{repo.github}:{pr.number}"
        with lock_for_repo(self.settings.lock_dir, lock_name) as lock:
            if not lock.acquired:
                log(f"Skipping {repo.github}#{pr.number}: PR feedback lock is already held")
                return
            approval = await latest_codex_approval(
                self.github,
                repo.github,
                pr.number,
                getattr(pr, "head_sha", ""),
            )
            mapped_issue = issue_identifier_for_pr(pr)
            feedback = await self.github.pr_feedback(repo.github, pr.number)
            failed_checks = await pr_failed_checks(
                self.github,
                repo.github,
                pr.number,
                getattr(pr, "head_sha", ""),
            )
            state = pr_feedback_state(self.settings.lock_dir, repo.github, pr.number)
            seen = read_processed_feedback(state)
            new_feedback = [item for item in feedback + failed_checks if item.key not in seen]
            issue_identifier = pr_feedback_issue_identifier(pr)
            if not new_feedback:
                log(f"{repo.github}#{pr.number}: no new PR feedback")
                if approval:
                    if mapped_issue:
                        changed = update_issue_codex_approval(mapped_issue, pr, approval)
                        if changed:
                            log(f"{repo.github}#{pr.number}: marked {mapped_issue} as Codex approved")
                    else:
                        log(f"{repo.github}#{pr.number}: Codex approved with no linked issue")
                    update_pr_status(
                        pr,
                        "No new feedback" if mapped_issue else "Codex approved",
                        repo_key=repo_key,
                        repo_path=repo.path,
                        feedback_count=0,
                        issue=mapped_issue,
                        codex_approval=approval,
                    )
                    if mapped_issue:
                        update_issue_pr_feedback_status(mapped_issue, pr, "No new feedback", 0)
                else:
                    if mapped_issue and clear_issue_codex_approval(mapped_issue, pr):
                        log(f"{repo.github}#{pr.number}: cleared stale Codex approval for {mapped_issue}")
                    update_pr_feedback_status(
                        pr,
                        "No new feedback",
                        issue=issue_identifier,
                        repo_key=repo_key,
                        repo_path=repo.path,
                        feedback_count=0,
                        clear_codex_approval=True,
                    )
                return
            log(f"{repo.github}#{pr.number}: found {len(new_feedback)} new PR feedback or failed check item(s)")
            issue_identifier = issue_identifier or mapped_issue
            if issue_identifier:
                ensure_issue_pr_feedback_status(issue_identifier, pr)
            if mapped_issue and clear_issue_codex_approval(mapped_issue, pr):
                log(f"{repo.github}#{pr.number}: cleared Codex approval for {mapped_issue} while handling feedback")
            update_pr_feedback_status(
                pr,
                "Feedback found",
                issue=issue_identifier,
                repo_key=repo_key,
                repo_path=repo.path,
                feedback_count=len(new_feedback),
                clear_codex_approval=True,
            )
            if self.settings.dry_run:
                log(f"[dry-run] Would address PR feedback on {repo.github}#{pr.number}")
                return

            run_git(repo.path, "fetch", "origin", pr.head_branch)
            run_git(repo.path, "checkout", "-B", pr.head_branch, f"origin/{pr.head_branch}")
            update_pr_feedback_status(
                pr,
                "Fixing feedback",
                issue=issue_identifier,
                repo_key=repo_key,
                repo_path=repo.path,
                feedback_count=len(new_feedback),
                clear_codex_approval=True,
            )
            summary = await self._fix_pr_feedback(repo_key, repo, pr, new_feedback)
            if has_changes(repo.path):
                log(f"{repo.github}#{pr.number}: committing PR feedback fixes")
                commit_all(repo.path, f"Address PR feedback for #{pr.number}")
                log(f"{repo.github}#{pr.number}: pushing PR feedback fixes")
                push_branch(repo.path, pr.head_branch)
                await self.github.comment_on_pr(repo.github, pr.number, pr_feedback_comment(summary))
                update_pr_feedback_status(
                    pr,
                    "Feedback addressed",
                    issue=issue_identifier,
                    repo_key=repo_key,
                    repo_path=repo.path,
                    feedback_count=len(new_feedback),
                    clear_codex_approval=True,
                )
            else:
                log(f"{repo.github}#{pr.number}: no changes after PR feedback pass")
                await self.github.comment_on_pr(
                    repo.github,
                    pr.number,
                    pr_feedback_no_changes_comment(summary),
                )
                update_pr_feedback_status(
                    pr,
                    "Checked feedback",
                    issue=issue_identifier,
                    repo_key=repo_key,
                    repo_path=repo.path,
                    feedback_count=len(new_feedback),
                    clear_codex_approval=True,
                )
            write_processed_feedback(state, seen | {item.key for item in new_feedback})

    async def process_linear_feedback(self, issue: LinearIssue) -> bool:
        if self.settings.running_label in issue.labels or self.settings.blocked_label in issue.labels:
            log(f"Skipping {issue.identifier}: running or blocked label is present")
            return False
        try:
            workspace = self.resolve_workspace(issue)
        except RuntimeError as exc:
            log(f"Skipping {issue.identifier}: {exc}")
            return False
        branch = self.linear_feedback_branch(issue, workspace)
        if not self.linear_feedback_branch_available(workspace, branch):
            log(f"Skipping {issue.identifier}: branch {branch} is not available in any repo")
            return False
        lock_name = f"{issue.team_key}:{workspace.path}"
        with lock_for_repo(self.settings.lock_dir, lock_name) as lock:
            if not lock.acquired:
                log(f"Skipping {issue.identifier}: workspace lock is already held")
                return False
            comments = await self.linear.issue_comments(issue)
            state = linear_feedback_state(self.settings.lock_dir, issue.identifier)
            seen = read_processed_feedback(state)
            feedback = actionable_linear_feedback(comments, seen)
            if not feedback:
                log(f"{issue.identifier}: no new Linear feedback")
                update_issue_linear_feedback_status(issue, "No new Linear feedback", 0)
                return False
            log(f"{issue.identifier}: found {len(feedback)} new Linear feedback comment(s)")
            update_issue_linear_feedback_status(issue, "Linear feedback found", len(feedback))
            if self.settings.dry_run:
                log(f"[dry-run] Would address Linear feedback on {issue.identifier}")
                return True
            dirty_repos = self.dirty_workspace_repos(workspace)
            if dirty_repos:
                joined = ", ".join(dirty_repos)
                log(f"Skipping {issue.identifier}: workspace has uncommitted changes in: {joined}")
                update_issue_linear_feedback_status(issue, "Workspace dirty", len(feedback))
                return True
            issue_context = await self.linear.issue_context(issue)
            self.checkout_existing_branch_from_origin(workspace, branch)
            before_heads = self.repo_heads(workspace)
            update_issue_linear_feedback_status(issue, "Fixing Linear feedback", len(feedback))
            summary = await self._fix_linear_feedback(
                issue,
                workspace,
                issue_context,
                branch,
                feedback,
            )
            changed_repos = self.changed_repos_since_heads(workspace, before_heads)
            prs: list[PullRequest] = []
            for repo_key, repo in changed_repos.items():
                if has_changes(repo.path):
                    log(f"{issue.identifier}: committing Linear feedback fixes in {repo_key}")
                    commit_all(repo.path, f"{issue.identifier}: address Linear feedback")
                log(f"{issue.identifier}: pushing {repo_key} branch {branch}")
                push_branch(repo.path, branch)
                pr_body = linear_feedback_pr_description(issue, repo_key, repo.path, summary)
                log(f"{issue.identifier}: updating ready-for-review PR for {repo_key}")
                pr = await self.github.create_or_update_pr(
                    repo.github,
                    branch,
                    repo.base,
                    f"{issue.identifier}: {issue.title}",
                    pr_body,
                )
                open_pr = OpenPullRequest(
                    repo.github,
                    pr.number,
                    pr.url,
                    pr.title,
                    branch,
                    repo.base,
                )
                if clear_issue_codex_approval(issue.identifier, open_pr):
                    log(
                        f"{issue.identifier}: cleared stale Codex approval "
                        f"for {repo.github}#{pr.number}"
                    )
                update_pr_status(
                    open_pr,
                    "Updated for Linear feedback",
                    issue=issue.identifier,
                    repo_key=repo_key,
                    repo_path=repo.path,
                    clear_codex_approval=True,
                )
                prs.append(pr)
            if changed_repos:
                await self._try_linear_comment(issue, linear_feedback_comment(summary, prs))
                update_issue_linear_feedback_status(issue, "Linear feedback addressed", len(feedback))
            else:
                await self._try_linear_comment(issue, linear_feedback_no_changes_comment(summary))
                update_issue_linear_feedback_status(issue, "Checked Linear feedback", len(feedback))
            write_processed_feedback(state, seen | {item.key for item in feedback})
            return True

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
                f"No configured workspace entry for Linear team key {issue.team_key}."
            ) from exc

    async def interrupted_issues(self) -> list[LinearIssue]:
        candidates = await self.linear.ready_issues(
            self.settings.in_progress_status,
            None,
            max(self.settings.max_issues_per_tick * 10, 10),
            (self.settings.running_label, self.settings.blocked_label),
            tuple(sorted(self.settings.workspace_map)),
        )
        resumable: list[LinearIssue] = []
        for issue in candidates:
            try:
                workspace = self.resolve_workspace(issue)
            except RuntimeError as exc:
                log(f"Skipping interrupted candidate {issue.identifier}: {exc}")
                continue
            branch = branch_name(issue.identifier, issue.title)
            if self.branch_exists_in_all_repos(workspace, branch):
                resumable.append(issue)
                if len(resumable) >= self.settings.max_issues_per_tick:
                    break
            else:
                log(f"Skipping interrupted candidate {issue.identifier}: branch {branch} is not present in all repos")
        return resumable

    async def _process_locked_issue(self, issue: LinearIssue, workspace: WorkspaceConfig, resume: bool = False) -> None:
        branch = branch_name(issue.identifier, issue.title)

        def set_issue_status(status: str, **extra: object) -> None:
            update_issue_status(issue, status, branch=branch, **extra)

        repo_list = ", ".join(workspace.repos)
        mode = "resuming" if resume else "processing"
        log(f"{mode.capitalize()} {issue.identifier} in {workspace.path} across: {repo_list}")
        set_issue_status(
            "Resuming" if resume else "Starting",
            description=issue.description,
            context_status="metadata",
            **workspace_status_context(workspace),
        )
        if self.settings.dry_run:
            action = "resume branch" if resume else "create branch"
            log(f"[dry-run] Would {action} {branch} and run Codex across {repo_list}")
            set_issue_status("Dry run")
            return
        if not resume:
            dirty_repos = self.dirty_workspace_repos(workspace)
            if dirty_repos:
                joined = ", ".join(dirty_repos)
                log(f"Skipping {issue.identifier}: workspace has uncommitted changes in: {joined}")
                set_issue_status(
                    "Workspace dirty",
                    dirty_repos=joined,
                    **workspace_status_context(workspace),
                )
                return

        try:
            log(f"{issue.identifier}: reading full Linear issue context")
            issue_context = await self.linear.issue_context(issue)
            set_issue_status(
                "Linear context loaded",
                issue_context=issue_context,
                context_status="linear_context",
                **workspace_status_context(workspace),
            )
            run_state = read_issue_run_state(issue.id, workspace.path) if resume else None
            if resume:
                log(f"{issue.identifier}: resuming existing branch {branch}")
                set_issue_status("Resuming branch")
                self.checkout_existing_branch(workspace, branch)
                plan = run_state.plan if run_state and run_state.plan else resume_plan()
                implementation_summary = (
                    run_state.implementation_summary
                    if run_state and run_state.implementation_summary
                    else "Resumed from existing repository changes after an interrupted previous run."
                )
                resume_stage = run_state.stage if run_state else ""
                if resume_stage:
                    log(f"{issue.identifier}: local run-state stage is {resume_stage}")
                if resume_stage not in STAGES_AFTER_IMPLEMENTATION:
                    write_issue_run_state(
                        issue.id,
                        issue.identifier,
                        workspace.path,
                        branch,
                        "implementing",
                        plan=plan,
                    )
                    log(f"{issue.identifier}: resumed implementation started")
                    set_issue_status("Implementing")
                    implementation_summary = await self._implement(issue, workspace, issue_context, plan)
                    log(f"{issue.identifier}: resumed implementation finished; detecting changed repos")
                    changed_repos = self.changed_repos(workspace)
                    self.commit_phase_changes(issue, changed_repos, "implementation")
                    write_issue_run_state(
                        issue.id,
                        issue.identifier,
                        workspace.path,
                        branch,
                        "implemented",
                        plan=plan,
                        implementation_summary=implementation_summary,
                    )
                else:
                    log(f"{issue.identifier}: skipping implementation; resuming after {resume_stage}")
                    changed_repos = self.changed_repos(workspace)
                log(f"{issue.identifier}: changed repos: {', '.join(changed_repos) or 'none'}")
                set_issue_status("Implemented", changed_repos=", ".join(changed_repos) or "none")
                if resume_stage not in STAGES_AFTER_IMPLEMENTATION:
                    await self._try_linear_comment(issue, implementation_comment(changed_repos, implementation_summary))
            else:
                log(f"{issue.identifier}: posting start comment")
                await self.linear.comment(issue.id, start_comment(issue, workspace, branch))
                log(f"{issue.identifier}: planning")
                set_issue_status("Planning")
                write_issue_run_state(issue.id, issue.identifier, workspace.path, branch, "planning")
                plan = await self._plan(issue, workspace, issue_context)
                set_issue_status(
                    "Planning complete",
                    planner_brief=plan,
                    context_status="planned",
                    **workspace_status_context(workspace),
                )
                log(f"{issue.identifier}: preparing branch {branch} in {len(workspace.repos)} repo(s)")
                set_issue_status("Preparing branches")
                for repo_key, repo in workspace.repos.items():
                    log(f"{issue.identifier}: ensuring {repo_key} branch {branch}")
                    ensure_branch(repo.path, repo.base, branch)
                write_issue_run_state(
                    issue.id,
                    issue.identifier,
                    workspace.path,
                    branch,
                    "branch_prepared",
                    plan=plan,
                )
                log(f"{issue.identifier}: posting plan and moving to {self.settings.in_progress_status}")
                await self.linear.comment(issue.id, plan_comment(plan))
                await self.linear.move_issue(issue.id, self.settings.in_progress_status)
                await self.linear.add_label(issue.id, self.settings.running_label)

                log(f"{issue.identifier}: implementation started")
                set_issue_status("Implementing")
                write_issue_run_state(
                    issue.id,
                    issue.identifier,
                    workspace.path,
                    branch,
                    "implementing",
                    plan=plan,
                )
                implementation_summary = await self._implement(issue, workspace, issue_context, plan)
                log(f"{issue.identifier}: implementation finished; detecting changed repos")
                changed_repos = self.changed_repos(workspace)
                log(f"{issue.identifier}: changed repos: {', '.join(changed_repos) or 'none'}")
                set_issue_status("Implemented", changed_repos=", ".join(changed_repos) or "none")
                self.commit_phase_changes(issue, changed_repos, "implementation")
                write_issue_run_state(
                    issue.id,
                    issue.identifier,
                    workspace.path,
                    branch,
                    "implemented",
                    plan=plan,
                    implementation_summary=implementation_summary,
                )
                await self._try_linear_comment(issue, implementation_comment(changed_repos, implementation_summary))
            if changed_repos:
                if resume and run_state and run_state.stage in STAGES_AFTER_OPTIMIZATION:
                    log(f"{issue.identifier}: skipping optimization; resuming after {run_state.stage}")
                else:
                    log(f"{issue.identifier}: optimization started")
                    set_issue_status("Optimizing", changed_repos=", ".join(changed_repos))
                    write_issue_run_state(
                        issue.id,
                        issue.identifier,
                        workspace.path,
                        branch,
                        "optimizing",
                        plan=plan,
                        implementation_summary=implementation_summary,
                    )
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
                    set_issue_status("Optimized", changed_repos=", ".join(changed_repos) or "none")
                    self.commit_phase_changes(issue, changed_repos, "optimization")
                    write_issue_run_state(
                        issue.id,
                        issue.identifier,
                        workspace.path,
                        branch,
                        "optimized",
                        plan=plan,
                        implementation_summary=implementation_summary,
                    )
                    await self._try_linear_comment(issue, optimization_comment(optimization_summary))
            log(f"{issue.identifier}: review started")
            set_issue_status("Reviewing", changed_repos=", ".join(changed_repos) or "none")
            write_issue_run_state(
                issue.id,
                issue.identifier,
                workspace.path,
                branch,
                "reviewing",
                plan=plan,
                implementation_summary=implementation_summary,
            )
            review = await self._review(issue, workspace, issue_context, plan, changed_repos)
            log(f"{issue.identifier}: review {'passed' if review.passed else 'failed'}")
            set_issue_status("Review passed" if review.passed else "Review failed")
            await self._try_linear_comment(issue, review_comment(review))
            if changed_repos and not review.passed:
                log(f"{issue.identifier}: reviewer-fix pass started")
                set_issue_status("Fixing review findings")
                write_issue_run_state(
                    issue.id,
                    issue.identifier,
                    workspace.path,
                    branch,
                    "review_fixing",
                    plan=plan,
                    implementation_summary=implementation_summary,
                )
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
                self.commit_phase_changes(issue, changed_repos, "review fixes")
                write_issue_run_state(
                    issue.id,
                    issue.identifier,
                    workspace.path,
                    branch,
                    "review_fixed",
                    plan=plan,
                    implementation_summary=implementation_summary,
                )
                await self._try_linear_comment(issue, review_fix_comment(fix_summary))
                review = await self._review(issue, workspace, issue_context, plan, changed_repos)
                log(f"{issue.identifier}: re-review {'passed' if review.passed else 'failed'}")
                set_issue_status("Re-review passed" if review.passed else "Re-review failed")
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
            set_issue_status("Blocked")
            return
        except Exception as exc:
            log(f"{issue.identifier}: failed: {exc}")
            set_issue_status("Failed", error=str(exc))
            await self._try_linear_comment(issue, f"Codex orchestration failed:\n\n```text\n{exc}\n```")
            if not resume:
                await self._clear_running_label(issue)
            raise

        if not changed_repos:
            await self._try_linear_comment(issue, "Codex completed the run, but no git changes exist.")
            await self._clear_running_label(issue)
            clear_issue_run_state(issue.id, workspace.path)
            log(f"{issue.identifier}: no changes detected; stopping")
            set_issue_status("No changes")
            return

        if not review.passed:
            await self._try_linear_comment(
                issue,
                f"Codex reviewer did not approve an automatic PR yet.\n\n{review.summary}",
            )
            await self._clear_running_label(issue)
            clear_issue_run_state(issue.id, workspace.path)
            log(f"{issue.identifier}: reviewer blocked automatic PR")
            set_issue_status("Reviewer blocked PR")
            return

        write_issue_run_state(
            issue.id,
            issue.identifier,
            workspace.path,
            branch,
            "pr_creating",
            plan=plan,
            implementation_summary=implementation_summary,
        )
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
        await self._try_seed_linear_feedback_state(issue)
        await self._try_linear_action(
            issue,
            f"move to {self.settings.in_review_status}",
            self.linear.move_issue(issue.id, self.settings.in_review_status),
        )
        await self._clear_running_label(issue)
        clear_issue_run_state(issue.id, workspace.path)
        log(f"{issue.identifier}: opened/updated {len(prs)} PR(s)")
        set_issue_status("PR ready", prs=", ".join(pr.url for pr in prs))

    async def _try_linear_comment(self, issue: LinearIssue, body: str) -> bool:
        return await self._try_linear_action(
            issue,
            "post Linear comment",
            self.linear.comment(issue.id, mark_linear_orchestrator_comment(body)),
        )

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

    def changed_repos_since_heads(
        self,
        workspace: WorkspaceConfig,
        before_heads: dict[str, str],
    ) -> dict[str, RepoConfig]:
        return {
            repo_key: repo
            for repo_key, repo in workspace.repos.items()
            if has_changes(repo.path) or self.repo_head(repo) != before_heads.get(repo_key, "")
        }

    def repo_heads(self, workspace: WorkspaceConfig) -> dict[str, str]:
        return {
            repo_key: self.repo_head(repo)
            for repo_key, repo in workspace.repos.items()
        }

    def repo_head(self, repo: RepoConfig) -> str:
        try:
            return run_git(repo.path, "rev-parse", "HEAD")
        except Exception:
            return ""

    def dirty_workspace_repos(self, workspace: WorkspaceConfig) -> list[str]:
        return [
            repo_key
            for repo_key, repo in workspace.repos.items()
            if has_changes(repo.path)
        ]

    def uncommitted_changed_repos(self, workspace: WorkspaceConfig) -> dict[str, RepoConfig]:
        return {
            repo_key: repo
            for repo_key, repo in workspace.repos.items()
            if has_changes(repo.path)
        }

    def commit_phase_changes(
        self,
        issue: LinearIssue,
        changed_repos: dict[str, RepoConfig],
        phase: str,
    ) -> None:
        for repo_key, repo in changed_repos.items():
            if has_changes(repo.path):
                log(f"{issue.identifier}: committing {phase} changes in {repo_key}")
                commit_all(repo.path, f"{issue.identifier}: {phase}")

    def checkout_existing_branch(self, workspace: WorkspaceConfig, branch: str) -> None:
        missing: list[str] = []
        failed: list[str] = []
        for repo_key, repo in workspace.repos.items():
            if not branch_exists(repo.path, branch):
                missing.append(repo_key)
                continue
            if not checkout_branch(repo.path, branch):
                failed.append(repo_key)
        if missing:
            raise RuntimeError(f"Cannot resume because branch {branch} is missing in: {', '.join(missing)}")
        if failed:
            raise RuntimeError(f"Cannot resume because branch {branch} could not be checked out in: {', '.join(failed)}")

    def checkout_existing_branch_from_origin(self, workspace: WorkspaceConfig, branch: str) -> None:
        failed: list[str] = []
        for repo_key, repo in workspace.repos.items():
            if remote_branch_exists(repo.path, branch):
                try:
                    run_git(repo.path, "fetch", "origin", branch)
                    run_git(repo.path, "checkout", "-B", branch, f"origin/{branch}")
                except Exception:
                    failed.append(repo_key)
            elif branch_exists(repo.path, branch):
                log(f"{repo_key}: branch {branch} has no origin branch; using local branch")
                if not checkout_branch(repo.path, branch):
                    failed.append(repo_key)
            else:
                log(f"{repo_key}: branch {branch} is missing; recreating it from {repo.base}")
                try:
                    ensure_branch(repo.path, repo.base, branch)
                except Exception:
                    failed.append(repo_key)
        if failed:
            raise RuntimeError(
                f"Cannot refresh branch {branch} from origin in: {', '.join(failed)}"
            )

    def branch_exists_in_all_repos(self, workspace: WorkspaceConfig, branch: str) -> bool:
        return all(branch_exists(repo.path, branch) for repo in workspace.repos.values())

    def branch_available_in_all_repos(self, workspace: WorkspaceConfig, branch: str) -> bool:
        return all(
            branch_exists(repo.path, branch) or remote_branch_exists(repo.path, branch)
            for repo in workspace.repos.values()
        )

    def linear_feedback_branch_available(self, workspace: WorkspaceConfig, branch: str) -> bool:
        return any(
            branch_exists(repo.path, branch) or remote_branch_exists(repo.path, branch)
            for repo in workspace.repos.values()
        )

    def linear_feedback_branch(self, issue: LinearIssue, workspace: WorkspaceConfig) -> str:
        status_branch = self.linear_feedback_status_branch(issue, workspace)
        if status_branch:
            return status_branch
        return branch_name(issue.identifier, issue.title)

    def linear_feedback_status_branch(self, issue: LinearIssue, workspace: WorkspaceConfig) -> str:
        payload = read_status()
        prs = payload["prs"]
        assert isinstance(prs, dict)
        repo_paths = {str(repo.path) for repo in workspace.repos.values()}
        repo_names = {repo.github for repo in workspace.repos.values()}
        for value in prs.values():
            if not isinstance(value, dict) or value.get("archived"):
                continue
            if str(value.get("issue") or "").strip().upper() != issue.identifier.upper():
                continue
            repo_path = str(value.get("repo_path") or "")
            repo_name = str(value.get("repo") or "")
            if repo_path not in repo_paths and repo_name not in repo_names:
                continue
            branch = str(value.get("branch") or "").strip()
            if branch and self.linear_feedback_branch_available(workspace, branch):
                return branch
        return ""

    async def seed_linear_feedback_state(self, issue: LinearIssue) -> None:
        state = linear_feedback_state(self.settings.lock_dir, issue.identifier)
        if state.exists():
            return
        comments = await self.linear.issue_comments(issue)
        baseline = {item.key for item in actionable_linear_feedback(comments, set())}
        write_processed_feedback(state, baseline)
        log(
            f"{issue.identifier}: seeded Linear feedback state "
            f"with {len(baseline)} existing comment(s)"
        )

    async def _try_seed_linear_feedback_state(self, issue: LinearIssue) -> bool:
        try:
            await self.seed_linear_feedback_state(issue)
            return True
        except Exception as exc:
            log(
                f"{issue.identifier}: failed to seed Linear feedback state; "
                f"daemon will continue: {exc}"
            )
            return False

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

    async def _fix_linear_feedback(
        self,
        issue: LinearIssue,
        workspace: WorkspaceConfig,
        issue_context: str,
        branch: str,
        feedback: list[LinearCommentFeedback],
    ) -> str:
        log_path = codex_log_path(issue.identifier, "linear-feedback")
        log(f"{issue.identifier}: Linear feedback output: {log_path}")
        return run_codex(
            linear_feedback_prompt(issue, workspace, issue_context, branch, feedback),
            workspace.path,
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


def resume_plan() -> str:
    return """
Resume interrupted automation from the existing branch and working tree.

Before editing, inspect the current branch, git status, and existing diffs.
Treat any uncommitted files and branch commits as partial implementation work.
Continue the implementation from that state instead of restarting, optimizing,
or reviewing first. Preserve intentional existing changes unless the Linear
issue requires adjusting them.
""".strip()


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


def linear_feedback_prompt(
    issue: LinearIssue,
    workspace: WorkspaceConfig,
    issue_context: str,
    branch: str,
    feedback: list[LinearCommentFeedback],
) -> str:
    feedback_text = "\n\n".join(linear_feedback_item_text(item) for item in feedback)
    repos = "\n".join(
        f"- {repo_key}: {repo.github} at {repo.path} (base {repo.base})"
        for repo_key, repo in workspace.repos.items()
    )
    return render_prompt(
        "linear_feedback_fix.md",
        workspace_path=workspace.path,
        issue_identifier=issue.identifier,
        issue_title=issue.title,
        issue_url=issue.url,
        issue_context=issue_context,
        branch=branch,
        repositories=repos,
        feedback=feedback_text,
    )


def linear_feedback_item_text(item: LinearCommentFeedback) -> str:
    return f"""
### Linear comment by {item.author}

Comment ID: `{item.id}`
Updated: `{item.updated_at}`
URL: {item.url}

```text
{item.body.strip()}
```
""".strip()


def pr_feedback_item_text(item: PullRequestFeedback) -> str:
    path = f"\nPath: `{item.path}`" if item.path else ""
    return f"""
### {item.kind} by {item.author}

URL: {item.url}{path}

```text
{item.body.strip()}
```
""".strip()


def linear_feedback_pr_description(
    issue: LinearIssue,
    repo_key: str,
    repo_path: Path,
    summary: str,
) -> str:
    try:
        diffstat = run_git(repo_path, "diff", "--stat", "HEAD~1..HEAD")
    except Exception:
        diffstat = "Diffstat unavailable."
    return f"""
Linear issue: {issue.url}

Repository: {repo_key}

## Linear Feedback Pass
{summary}

## Diffstat
```text
{diffstat}
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


def linear_feedback_comment(summary: str, prs: list[PullRequest]) -> str:
    links = "\n".join(f"- {pr.url}" for pr in prs)
    return f"""
Codex addressed new Linear feedback.

Updated pull requests:
{links}

{truncate_markdown(summary)}
""".strip()


def linear_feedback_no_changes_comment(summary: str) -> str:
    return f"""
Codex checked the new Linear feedback and did not make repository changes.

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
    issues = _status_map(payload, "issues")
    prs = _status_map(payload, "prs")
    legacy_archived_prs = _status_map(payload, "archived_prs")
    for key, value in legacy_archived_prs.items():
        if isinstance(value, dict) and key not in prs:
            prs[key] = {**value, "archived": True}
    return {
        "issues": issues,
        "prs": prs,
        "archived_prs": {key: value for key, value in prs.items() if isinstance(value, dict) and value.get("archived")},
    }


def _status_map(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key, {})
    return value if isinstance(value, dict) else {}


def write_status(payload: dict[str, object]) -> None:
    stored = {key: value for key, value in payload.items() if key != "archived_prs"}
    status_path().parent.mkdir(parents=True, exist_ok=True)
    with status_path().open("w", encoding="utf-8") as handle:
        json.dump(stored, handle, indent=2, sort_keys=True)
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
    codex_approval: PullRequestApproval | None = None,
    clear_codex_approval: bool = False,
) -> None:
    payload = read_status()
    prs = payload["prs"]
    assert isinstance(prs, dict)
    key = f"{pr.repo}#{pr.number}"
    current = prs.get(key, {})
    if not isinstance(current, dict):
        current = {}
    current.pop("archived", None)
    current.pop("archived_at", None)
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
    if clear_codex_approval:
        current.pop("codex_approved", None)
        current.pop("codex_approved_at", None)
        current.pop("codex_approval_url", None)
        current.pop("codex_approved_pr", None)
    if codex_approval:
        current["codex_approved"] = True
        current["codex_approved_at"] = (
            codex_approval.submitted_at or datetime.now().isoformat(timespec="seconds")
        )
        current["codex_approval_url"] = codex_approval.url
        current["codex_approved_pr"] = pr.url
    prs[key] = current
    write_status(payload)


def issue_identifier_for_pr(pr: OpenPullRequest) -> str | None:
    payload = read_status()
    prs = payload["prs"]
    assert isinstance(prs, dict)
    current = prs.get(f"{pr.repo}#{pr.number}", {})
    if isinstance(current, dict):
        issue = current.get("issue")
        if isinstance(issue, str) and issue.strip():
            return issue.strip().upper()
    return parse_issue_identifier(pr.title) or parse_issue_identifier(pr.head_branch)


def parse_issue_identifier(value: str) -> str | None:
    match = re.search(r"\b([A-Z][A-Z0-9]+-\d+)\b", value, re.IGNORECASE)
    return match.group(1).upper() if match else None


def update_issue_codex_approval(
    issue_identifier: str,
    pr: OpenPullRequest,
    approval: PullRequestApproval,
) -> bool:
    payload = read_status()
    issues = payload["issues"]
    assert isinstance(issues, dict)
    current = issues.get(issue_identifier, {})
    if not isinstance(current, dict):
        current = {}
    approved_at = approval.submitted_at or datetime.now().isoformat(timespec="seconds")
    next_values = {
        "identifier": current.get("identifier") or issue_identifier,
        "title": current.get("title") or issue_identifier,
        "status": current.get("status") or "Codex approved",
        "codex_approved": True,
        "codex_approved_at": approved_at,
        "codex_approval_url": approval.url,
        "codex_approved_pr": pr.url,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    changed = any(
        current.get(key) != value
        for key, value in next_values.items()
        if key != "updated_at"
    )
    if not changed:
        return False
    current.update(next_values)
    issues[issue_identifier] = current
    write_status(payload)
    return True


def clear_issue_codex_approval(issue_identifier: str, pr: OpenPullRequest) -> bool:
    payload = read_status()
    issues = payload["issues"]
    assert isinstance(issues, dict)
    current = issues.get(issue_identifier, {})
    if not isinstance(current, dict) or current.get("codex_approved_pr") != pr.url:
        return False
    for key in (
        "codex_approved",
        "codex_approved_at",
        "codex_approval_url",
        "codex_approved_pr",
    ):
        current.pop(key, None)
    if current.get("status") == "Codex approved":
        current["status"] = "PR ready"
    current["updated_at"] = datetime.now().isoformat(timespec="seconds")
    issues[issue_identifier] = current
    write_status(payload)
    return True


async def latest_codex_approval(
    github: object,
    repo: str,
    number: int,
    head_sha: str = "",
) -> PullRequestApproval | None:
    pr_codex_approvals = getattr(github, "pr_codex_approvals", None)
    if not callable(pr_codex_approvals):
        return None
    if not head_sha:
        return None
    approvals = await pr_codex_approvals(repo, number)
    approvals = [approval for approval in approvals if approval.commit_id == head_sha]
    if not approvals:
        return None
    return max(approvals, key=lambda item: item.submitted_at or item.key)


async def pr_failed_checks(
    github: object,
    repo: str,
    number: int,
    head_sha: str = "",
) -> list[PullRequestFeedback]:
    failed_checks = getattr(github, "pr_failed_checks", None)
    if not callable(failed_checks):
        return []
    if not head_sha:
        return []
    return await failed_checks(repo, number, head_sha)


def update_pr_feedback_status(
    pr: OpenPullRequest,
    status: str,
    *,
    issue: str | None = None,
    repo_key: str | None = None,
    repo_path: Path | None = None,
    feedback_count: int | None = None,
    codex_approval: PullRequestApproval | None = None,
    clear_codex_approval: bool = False,
) -> None:
    update_pr_status(
        pr,
        status,
        issue=issue,
        repo_key=repo_key,
        repo_path=repo_path,
        feedback_count=feedback_count,
        codex_approval=codex_approval,
        clear_codex_approval=clear_codex_approval,
    )
    if issue:
        update_issue_pr_feedback_status(issue, pr, status, feedback_count)


def update_issue_pr_feedback_status(
    issue_identifier: str,
    pr: OpenPullRequest,
    status: str,
    feedback_count: int | None = None,
) -> None:
    payload = read_status()
    issues = payload["issues"]
    assert isinstance(issues, dict)
    current = issues.get(issue_identifier)
    if not isinstance(current, dict):
        return
    current["prs"] = append_csv_value(str(current.get("prs") or ""), pr.url)
    current["pr_feedback"] = pr_feedback_status_text(pr, status, feedback_count)
    current["pr_feedback_updated_at"] = datetime.now().isoformat(timespec="seconds")
    issues[issue_identifier] = current
    write_status(payload)


def update_issue_linear_feedback_status(
    issue: LinearIssue,
    status: str,
    feedback_count: int | None = None,
) -> None:
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
        }
    )
    current["linear_feedback"] = linear_feedback_status_text(status, feedback_count)
    if feedback_count is not None:
        current["linear_feedback_count"] = feedback_count
    current["linear_feedback_updated_at"] = datetime.now().isoformat(timespec="seconds")
    current["updated_at"] = current["linear_feedback_updated_at"]
    if status != "No new Linear feedback":
        current["status"] = status
    issues[issue.identifier] = current
    write_status(payload)


def ensure_issue_pr_feedback_status(issue_identifier: str, pr: OpenPullRequest) -> None:
    payload = read_status()
    issues = payload["issues"]
    assert isinstance(issues, dict)
    current = issues.get(issue_identifier)
    if isinstance(current, dict):
        return
    issues[issue_identifier] = {
        "identifier": issue_identifier,
        "title": issue_identifier,
        "status": "PR feedback",
        "prs": pr.url,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_status(payload)


def pr_feedback_issue_identifier(pr: OpenPullRequest) -> str | None:
    payload = read_status()
    prs = payload["prs"]
    issues = payload["issues"]
    assert isinstance(prs, dict)
    assert isinstance(issues, dict)
    current = prs.get(f"{pr.repo}#{pr.number}")
    if isinstance(current, dict):
        existing = current.get("issue")
        if isinstance(existing, str) and existing.strip():
            return existing.strip()
    inferred = issue_identifier_from_pr(pr)
    return inferred if inferred in issues else None


def issue_identifier_from_pr(pr: OpenPullRequest) -> str | None:
    for value in (pr.title, pr.head_branch):
        match = re.search(r"\b([A-Z]+-\d+)\b", value, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return None


def append_csv_value(current: str, value: str) -> str:
    values = [item.strip() for item in current.split(",") if item.strip()]
    if value not in values:
        values.append(value)
    return ", ".join(values)


def pr_feedback_status_text(
    pr: OpenPullRequest,
    status: str,
    feedback_count: int | None = None,
) -> str:
    suffix = "" if feedback_count is None else f" ({feedback_count} item{'s' if feedback_count != 1 else ''})"
    return f"{pr.repo}#{pr.number}: {status}{suffix}"


def linear_feedback_status_text(status: str, feedback_count: int | None = None) -> str:
    suffix = "" if feedback_count is None else f" ({feedback_count} comment{'s' if feedback_count != 1 else ''})"
    return f"{status}{suffix}"


async def archive_stale_prs(
    github: object,
    repo: str,
    open_prs: list[OpenPullRequest],
) -> None:
    payload = read_status()
    prs = payload["prs"]
    archived_prs = payload["archived_prs"]
    assert isinstance(prs, dict)
    assert isinstance(archived_prs, dict)

    open_keys = {f"{pr.repo}#{pr.number}" for pr in open_prs}
    stale_keys = [
        key
        for key, value in prs.items()
        if key not in open_keys and isinstance(value, dict) and pr_entry_repo(key, value) == repo
    ]
    if not stale_keys:
        return

    archived_at = datetime.now().isoformat(timespec="seconds")
    for key in stale_keys:
        current = prs[key]
        assert isinstance(current, dict)
        status = "Archived"
        number = pr_entry_number(key, current)
        if number is not None and hasattr(github, "pr_archive_status"):
            try:
                status = await github.pr_archive_status(repo, number)
            except Exception:
                status = "Archived"
        current.update(
            {
                "status": status,
                "archived": True,
                "archived_at": archived_at,
                "updated_at": archived_at,
            }
        )
        archived_prs[key] = current
    sync_merged_issue_statuses(payload)
    write_status(payload)


def pr_entry_repo(key: str, entry: dict[str, object]) -> str | None:
    repo = entry.get("repo")
    if isinstance(repo, str):
        return repo
    if "#" in key:
        return key.rsplit("#", 1)[0]
    return None


def pr_entry_number(key: str, entry: dict[str, object]) -> int | None:
    number = entry.get("number")
    if isinstance(number, int):
        return number
    if isinstance(number, str) and number.isdigit():
        return int(number)
    match = re.search(r"#(\d+)$", key)
    return int(match.group(1)) if match else None


def archive_pr_status(pr: OpenPullRequest) -> bool:
    payload = read_status()
    prs = payload["prs"]
    assert isinstance(prs, dict)
    key = f"{pr.repo}#{pr.number}"
    current = prs.get(key)
    existed = isinstance(current, dict) and not current.get("archived")
    if existed:
        archived_at = datetime.now().isoformat(timespec="seconds")
        current.update(
            {
                "status": "Merged",
                "archived": True,
                "archived_at": archived_at,
                "updated_at": archived_at,
            }
        )
        sync_merged_issue_statuses(payload)
    write_status(payload)
    return existed


def sync_merged_issue_statuses(payload: dict[str, object]) -> None:
    issues = payload.get("issues", {})
    prs = payload.get("prs", {})
    if not isinstance(issues, dict) or not isinstance(prs, dict):
        return
    issue_identifiers = sorted(
        {
            issue.strip()
            for value in prs.values()
            if isinstance(value, dict)
            for issue in [value.get("issue")]
            if isinstance(issue, str) and issue.strip()
        }
    )
    now = datetime.now().isoformat(timespec="seconds")
    for issue_identifier in issue_identifiers:
        issue = issues.get(issue_identifier)
        if not isinstance(issue, dict):
            continue
        associated_prs = [
            value
            for value in prs.values()
            if isinstance(value, dict) and value.get("issue") == issue_identifier
        ]
        if associated_prs and all(is_merged_pr_status(value) for value in associated_prs):
            issue["status"] = "Done"
            issue["merged_prs"] = ", ".join(
                str(value.get("url"))
                for value in associated_prs
                if isinstance(value.get("url"), str) and value.get("url")
            )
            issue["pr_merged_at"] = now
            issue["updated_at"] = now


def is_merged_pr_status(entry: dict[str, object]) -> bool:
    return bool(entry.get("archived")) and entry.get("status") == "Merged"


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


def linear_feedback_state(lock_dir: Path, issue_identifier: str) -> Path:
    safe_issue = re.sub(r"[^A-Za-z0-9_.-]+", "-", issue_identifier).strip("-").lower()
    return lock_dir / "linear-feedback-state" / f"{safe_issue}.json"


def actionable_linear_feedback(
    comments: list[LinearCommentFeedback],
    processed: set[str],
) -> list[LinearCommentFeedback]:
    return [
        comment
        for comment in comments
        if comment.key not in processed
        and comment.body.strip()
        and not is_orchestrator_linear_comment(comment.body)
    ]


def linear_client_settings_changed(old: Settings, new: Settings) -> bool:
    return (
        old.linear_api_key != new.linear_api_key
        or old.dry_run != new.dry_run
        or old.codex_model != new.codex_model
        or old.codex_reasoning_effort != new.codex_reasoning_effort
        or old.codex_fast_mode != new.codex_fast_mode
    )


def github_client_settings_changed(old: Settings, new: Settings) -> bool:
    return old.dry_run != new.dry_run


def read_processed_feedback(path: Path) -> set[str]:
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return set()
    except json.JSONDecodeError:
        return set()
    if not isinstance(payload, dict):
        return set()
    processed = payload.get("processed", [])
    if not isinstance(processed, list):
        return set()
    return {item for item in processed if isinstance(item, str)}


def write_processed_feedback(path: Path, processed: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump({"processed": sorted(processed)}, handle, indent=2)
        handle.write("\n")


class PlannerBlocked(Exception):
    def __init__(self, plan: str) -> None:
        super().__init__("Planner blocked automatic implementation")
        self.plan = plan
