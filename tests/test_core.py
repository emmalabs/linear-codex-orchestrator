from __future__ import annotations

import os
import tempfile
import unittest
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from linear_codex_orchestrator.codex_cli import _run_process_hidden, build_codex_command, parse_json_object
from linear_codex_orchestrator.config import Settings
from linear_codex_orchestrator.config import (
    RepoConfig,
    WorkspaceConfig,
    read_config_file,
    validate_workspace_map,
    write_config_file,
)
from linear_codex_orchestrator.git_ops import branch_name, has_commits_since_base, run_git
from linear_codex_orchestrator.local_github_client import (
    LocalGitHubClient,
    codex_approval_reviews,
    is_codex_approval_review,
    parse_gh_api_json,
    pull_request_number_from_url,
)
from linear_codex_orchestrator.linear_api_client import (
    LinearApiClient,
    linear_comment_from_node,
    issue_from_node,
    issue_matches_labels,
    render_issue_context,
    team_from_node,
)
from linear_codex_orchestrator.local_linear_client import LocalLinearClient, is_transient_linear_error
from linear_codex_orchestrator.log_summary import last_interesting_line, summarize_codex_log, tokens_used, write_log_summary
from linear_codex_orchestrator.orchestrator import (
    Orchestrator,
    actionable_linear_feedback,
    archive_stale_prs,
    archive_pr_status,
    clear_issue_codex_approval,
    codex_log_path,
    implementation_comment,
    latest_codex_approval,
    linear_feedback_prompt,
    linear_feedback_state,
    log_session_start,
    planner_block_reason,
    planner_blocked_comment,
    planner_is_blocked,
    pr_feedback_issue_identifier,
    pr_feedback_prompt,
    parse_issue_identifier,
    read_processed_feedback,
    read_status,
    resume_plan,
    start_comment,
    truncate_text,
    update_issue_codex_approval,
    update_issue_linear_feedback_status,
    update_issue_status,
    update_pr_feedback_status,
    update_pr_status,
    workspace_status_context,
    write_processed_feedback,
)
from linear_codex_orchestrator.models import (
    LinearCommentFeedback,
    LinearIssue,
    OpenPullRequest,
    PullRequest,
    PullRequestApproval,
    PullRequestFeedback,
    ReviewResult,
    is_orchestrator_linear_comment,
    mark_linear_orchestrator_comment,
    parse_linear_issue,
)
from linear_codex_orchestrator.models import LinearTeam
from linear_codex_orchestrator.locks import lock_file_is_stale, lock_for_repo
from linear_codex_orchestrator.prompt_templates import render_prompt
from linear_codex_orchestrator.run_state import (
    IssueRunState,
    clear_issue_run_state,
    read_issue_run_state,
    write_issue_run_state,
)
from linear_codex_orchestrator.web_server import (
    archive_status_item,
    browse_index,
    github_repo_index,
    render_missing_frontend,
    linear_teams_index,
    log_index,
    safe_frontend_path,
    safe_log_path,
    start_log_server,
    status_index,
    task_from_log_name,
    task_index,
    tail_text,
    config_index,
    update_status_item,
)


class CoreTests(unittest.TestCase):
    def test_branch_name_is_stable_and_safe(self) -> None:
        self.assertEqual(
            branch_name("ENG-123", "Fix OAuth callback: spaces & symbols!"),
            "codex/eng-123-fix-oauth-callback-spaces-symbols",
        )

    def test_lock_reclaims_dead_pid_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_dir = Path(tmp)
            lock_path = lock_dir / "repo.lock"
            lock_path.write_text("99999999", encoding="utf-8")

            self.assertTrue(lock_file_is_stale(lock_path))
            with lock_for_repo(lock_dir, "repo") as lock:
                self.assertTrue(lock.acquired)
                self.assertEqual(lock_path.read_text(encoding="utf-8"), str(os.getpid()))

            self.assertFalse(lock_path.exists())

    def test_lock_keeps_live_pid_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_dir = Path(tmp)
            lock_path = lock_dir / "repo.lock"
            lock_path.write_text(str(os.getpid()), encoding="utf-8")

            self.assertFalse(lock_file_is_stale(lock_path))
            with lock_for_repo(lock_dir, "repo") as lock:
                self.assertFalse(lock.acquired)

    def test_parse_linear_issue(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": None,
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "Todo"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": [{"name": "codex-ready"}]},
                "project": {"name": "Project X", "url": "https://linear.app/acme/project/project-x"},
            }
        )
        self.assertEqual(issue.description, "")
        self.assertEqual(issue.team_key, "ENG")
        self.assertEqual(issue.labels, ("codex-ready",))
        self.assertEqual(issue.project_name, "Project X")
        self.assertEqual(issue.project_url, "https://linear.app/acme/project/project-x")

    def test_settings_from_env_parses_repo_map(self) -> None:
        env = {
            "WORKSPACE_MAP_JSON": (
                '{"ENG":{"path":"/tmp/workspace","repos":'
                '{"web":{"github":"acme/web","path":"/tmp/web","base":"develop"}}}}'
            ),
            "DRY_RUN": "false",
            "LINEAR_API_KEY": "lin_api_test",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("linear_codex_orchestrator.config.read_config_file", return_value={}):
                with patch("linear_codex_orchestrator.config.validate_workspace_map"):
                    settings = Settings.from_env()
        self.assertFalse(settings.dry_run)
        self.assertFalse(settings.codex_fast_mode)
        self.assertTrue(settings.hot_reload_config)
        self.assertEqual(settings.linear_api_key, "lin_api_test")
        self.assertEqual(settings.pr_feedback_branch_prefix, "codex/")
        self.assertEqual(settings.workspace_map["ENG"].path, Path("/tmp/workspace"))
        self.assertEqual(settings.workspace_map["ENG"].repos["web"].github, "acme/web")
        self.assertEqual(settings.workspace_map["ENG"].repos["web"].path, Path("/tmp/web"))
        self.assertEqual(settings.workspace_map["ENG"].repos["web"].base, "develop")

    def test_settings_from_env_defaults_to_real_run(self) -> None:
        env = {
            "WORKSPACE_MAP_JSON": (
                '{"ENG":{"path":"/tmp/workspace","repos":'
                '{"web":{"github":"acme/web","path":"/tmp/web"}}}}'
            ),
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("linear_codex_orchestrator.config.read_config_file", return_value={}):
                with patch("linear_codex_orchestrator.config.validate_workspace_map"):
                    settings = Settings.from_env()
        self.assertFalse(settings.dry_run)

    def test_settings_from_env_parses_example_workspace(self) -> None:
        env = {
            "WORKSPACE_MAP_JSON": (
                '{"ENG":{"path":"/home/alex/Projects/product","repos":'
                '{"api":{"github":"example/product-api",'
                '"path":"/home/alex/Projects/product/api","base":"develop"}}}}'
            ),
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("linear_codex_orchestrator.config.read_config_file", return_value={}):
                with patch("linear_codex_orchestrator.config.validate_workspace_map"):
                    settings = Settings.from_env()
        self.assertEqual(settings.workspace_map["ENG"].repos["api"].github, "example/product-api")

    def test_settings_reads_sqlite_config_before_env(self) -> None:
        env = {
            "WORKSPACE_MAP_JSON": (
                '{"ENG":{"path":"/tmp/env-workspace","repos":'
                '{"web":{"github":"acme/env","path":"/tmp/env-web"}}}}'
            ),
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "app-workspace"
            repo = workspace / "api"
            repo.mkdir(parents=True)
            (repo / ".git").mkdir()
            config_path = tmp_path / "config.db"
            write_config_file(
                {
                    "workspace_map": {
                        "APP": {
                            "path": str(workspace),
                            "repos": {"api": {"github": "acme/api", "path": str(repo), "base": "develop"}},
                        }
                    },
                    "dry_run": True,
                },
                config_path,
            )
            self.assertEqual(read_config_file(config_path)["dry_run"], True)
            with patch.dict(os.environ, {**env, "ORCHESTRATOR_CONFIG_PATH": str(config_path)}, clear=True):
                with patch("linear_codex_orchestrator.config.validate_workspace_map"):
                    settings = Settings.from_env()
        self.assertTrue(settings.dry_run)
        self.assertIn("APP", settings.workspace_map)
        self.assertEqual(settings.workspace_map["APP"].repos["api"].github, "acme/api")

    def test_orchestrator_uses_linear_api_when_key_is_configured(self) -> None:
        settings = Settings(workspace_map={}, linear_api_key="lin_api_test")
        orchestrator = Orchestrator(settings, github=object())
        self.assertIsInstance(orchestrator.linear, LinearApiClient)

    def test_linear_api_filters_labels_client_side(self) -> None:
        node = {
            "labels": {"nodes": [{"name": "agent-ready"}, {"name": "Core"}]},
        }
        self.assertTrue(issue_matches_labels(node, "agent-ready", ("agent-blocked",)))
        self.assertFalse(issue_matches_labels(node, "missing", ()))
        self.assertFalse(issue_matches_labels(node, None, ("Core",)))

    def test_linear_api_issue_parsing_and_context_rendering(self) -> None:
        node = {
            "id": "abc",
            "identifier": "ENG-1",
            "title": "Ship",
            "description": "Do it",
            "url": "https://linear.app/acme/issue/ENG-1",
            "team": {"key": "ENG", "name": "Engineering"},
            "state": {"name": "Todo"},
            "labels": {"nodes": [{"name": "Core"}]},
            "project": {"name": "Project X", "url": "https://linear.app/acme/project/x"},
        }
        issue = issue_from_node(node)
        self.assertEqual(issue.identifier, "ENG-1")
        self.assertEqual(issue.project_name, "Project X")
        context = render_issue_context(
            {
                **node,
                "comments": {
                    "nodes": [
                        {
                            "createdAt": "2026-01-01T00:00:00Z",
                            "body": "Comment body",
                            "user": {"displayName": "Ada", "name": "ada"},
                        }
                    ]
                },
                "attachments": {"nodes": [{"title": "Spec", "url": "https://example.com/spec"}]},
            }
        )
        self.assertIn("# ENG-1: Ship", context)
        self.assertIn("Comment body", context)
        self.assertIn("Spec: https://example.com/spec", context)

    def test_linear_api_team_parsing_and_graphql_payload(self) -> None:
        self.assertEqual(team_from_node({"id": "team-id", "key": "eng", "name": "Engineering"}).key, "ENG")
        calls: list[tuple[str, dict[str, object]]] = []

        async def fake_graphql(query: str, variables: dict[str, object]) -> dict[str, object]:
            calls.append((query, variables))
            return {"teams": {"nodes": [{"id": "team-id", "key": "ENG", "name": "Engineering"}]}}

        client = LinearApiClient("lin_api_test")
        with patch.object(client, "_graphql", fake_graphql):
            teams = asyncio.run(client.teams())

        self.assertEqual(teams, [LinearTeam(id="team-id", key="ENG", name="Engineering")])
        self.assertIn("teams(first: 250)", calls[0][0])
        self.assertEqual(calls[0][1], {})

    def test_linear_api_issue_comments_paginates_all_pages(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "issue-id",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Review"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        calls: list[dict[str, object]] = []

        async def fake_graphql(_query: str, variables: dict[str, object]) -> dict[str, object]:
            calls.append(variables)
            after = variables.get("after")
            node_id = "first" if after is None else "second"
            return {
                "issue": {
                    "comments": {
                        "nodes": [
                            {
                                "id": node_id,
                                "body": f"{node_id} page",
                                "url": f"https://linear.app/acme/issue/ENG-1#comment-{node_id}",
                                "createdAt": f"2026-06-07T0{2 if after is None else 1}:00:00Z",
                                "updatedAt": f"2026-06-07T0{2 if after is None else 1}:00:00Z",
                                "user": {"displayName": "Reviewer", "name": "reviewer"},
                            }
                        ],
                        "pageInfo": {
                            "hasNextPage": after is None,
                            "endCursor": "cursor-1" if after is None else None,
                        },
                    }
                }
            }

        client = LinearApiClient("lin_api_test")
        with patch.object(client, "_graphql", fake_graphql):
            comments = asyncio.run(client.issue_comments(issue))

        self.assertEqual([call["after"] for call in calls], [None, "cursor-1"])
        self.assertEqual([comment.id for comment in comments], ["second", "first"])

    def test_validate_workspace_map_rejects_missing_paths(self) -> None:
        missing = Path("/definitely/missing/workspace")
        workspace_map = {
            "ENG": WorkspaceConfig(
                path=missing,
                repos={"web": RepoConfig("acme/web", missing / "web", "develop")},
            )
        }
        with self.assertRaisesRegex(RuntimeError, "Invalid WORKSPACE_MAP_JSON paths"):
            validate_workspace_map(workspace_map)

    def test_repo_lock_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = lock_for_repo(Path(tmp), "acme/web")
            second = lock_for_repo(Path(tmp), "acme/web")
            with first as acquired:
                self.assertTrue(acquired.acquired)
                with second as blocked:
                    self.assertFalse(blocked.acquired)

    def test_has_commits_since_base_detects_branch_commits(self) -> None:
        with tempfile.TemporaryDirectory() as remote_tmp, tempfile.TemporaryDirectory() as clone_tmp:
            remote = Path(remote_tmp)
            clone = Path(clone_tmp)
            run_git(remote, "init", "--bare")
            run_git(clone, "init")
            run_git(clone, "config", "user.email", "test@example.com")
            run_git(clone, "config", "user.name", "Test")
            run_git(clone, "remote", "add", "origin", str(remote))
            (clone / "README.md").write_text("base\n", encoding="utf-8")
            run_git(clone, "add", "README.md")
            run_git(clone, "commit", "-m", "base")
            run_git(clone, "branch", "-M", "develop")
            run_git(clone, "push", "-u", "origin", "develop")
            self.assertFalse(has_commits_since_base(clone, "develop"))
            run_git(clone, "checkout", "-b", "codex/eng-1")
            (clone / "README.md").write_text("branch\n", encoding="utf-8")
            run_git(clone, "commit", "-am", "branch")
            self.assertTrue(has_commits_since_base(clone, "develop"))

    def test_truncate_text_marks_truncated_content(self) -> None:
        self.assertEqual(truncate_text("short", 10), "short")
        self.assertEqual(truncate_text("0123456789abcdef", 10), "0123456789\n\n...[truncated]")

    def test_render_prompt_loads_markdown_template(self) -> None:
        prompt = render_prompt("implementation.md", **{
            "workspace_path": "/tmp/workspace",
            "issue_identifier": "ENG-1",
            "issue_title": "Test issue",
            "issue_url": "https://linear.app/example/issue/ENG-1",
            "issue_context": "Linear says add `SLM.screen.123` exactly.",
            "plan": "Do the smallest useful thing.",
        })
        self.assertIn("ENG-1", prompt)
        self.assertIn("/tmp/workspace", prompt)
        self.assertIn("Inspect the relevant repository code before editing", prompt)
        self.assertIn("Do not move or comment on Linear issues", prompt)
        self.assertIn("Note any assumptions, follow-ups, or blockers", prompt)
        self.assertIn("SLM.screen.123", prompt)
        self.assertIn("Planner scope is guidance", prompt)
        self.assertIn("use the configured Linear MCP tools to read issue", prompt)

    def test_implementation_comment_preserves_summary_context(self) -> None:
        comment = implementation_comment({}, "Changed the bucket rollover logic.\n\nValidation: tests passed.")
        self.assertIn("No repository changes were detected", comment)
        self.assertIn("Implementation context", comment)
        self.assertIn("Changed the bucket rollover logic", comment)

    def test_review_fix_prompt_loads_markdown_template(self) -> None:
        prompt = render_prompt("review_fix.md", **{
            "workspace_path": "/tmp/workspace",
            "issue_identifier": "ENG-1",
            "issue_title": "Test issue",
            "issue_url": "https://linear.app/example/issue/ENG-1",
            "issue_context": "Linear says add `SLM.screen.123` exactly.",
            "plan": "Do the smallest useful thing.",
            "changed_repos": "- api: /tmp/api",
            "review_summary": "Missing regression test.",
        })
        self.assertIn("Fix the reviewer findings", prompt)
        self.assertIn("Missing regression test", prompt)
        self.assertIn("Do not move or comment on Linear issues", prompt)
        self.assertIn("use the configured Linear MCP tools to read", prompt)

    def test_optimizer_prompt_loads_markdown_template(self) -> None:
        prompt = render_prompt("optimizer.md", **{
            "workspace_path": "/tmp/workspace",
            "issue_identifier": "ENG-1",
            "issue_title": "Test issue",
            "issue_url": "https://linear.app/example/issue/ENG-1",
            "issue_context": "Linear says add `SLM.screen.123` exactly.",
            "plan": "Do the smallest useful thing.",
            "changed_repos": "- api: /tmp/api",
            "implementation_summary": "Implemented bucket rollover.",
        })
        self.assertIn("Clean up and improve the implementation", prompt)
        self.assertIn("Preserve the implemented behavior", prompt)
        self.assertIn("Do not push, create pull requests, move Linear issues, or comment on Linear issues", prompt)
        self.assertIn("use the configured Linear MCP tools to read issue", prompt)

    def test_pr_feedback_prompt_loads_markdown_template(self) -> None:
        prompt = pr_feedback_prompt(
            "api",
            RepoConfig("acme/api", Path("/tmp/api"), "develop"),
            OpenPullRequest(
                repo="acme/api",
                number=12,
                url="https://github.com/acme/api/pull/12",
                title="ENG-1: Ship it",
                head_branch="codex/eng-1-ship-it",
                base_branch="develop",
            ),
            [
                PullRequestFeedback(
                    key="review-comment:1:2026-05-16T20:00:00Z",
                    kind="review comment",
                    author="reviewer",
                    body="Please add a regression test.",
                    url="https://github.com/acme/api/pull/12#discussion_r1",
                    path="src/app.py",
                )
            ],
        )
        self.assertIn("Address new GitHub PR feedback", prompt)
        self.assertIn("Please add a regression test.", prompt)
        self.assertIn("Path: `src/app.py`", prompt)
        self.assertIn("Do not commit, push, create pull requests, or comment on GitHub", prompt)

    def test_linear_feedback_prompt_loads_markdown_template(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Review"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        workspace = WorkspaceConfig(
            path=Path("/tmp/workspace"),
            repos={"api": RepoConfig("acme/api", Path("/tmp/workspace/api"), "develop")},
        )
        prompt = linear_feedback_prompt(
            issue,
            workspace,
            "# ENG-1: Ship it\n\nFull context",
            "codex/eng-1-ship-it",
            [
                LinearCommentFeedback(
                    key="linear-comment:c1:2026-06-07T09:00:00Z",
                    id="c1",
                    author="reviewer",
                    body="Please cover the retry edge case.",
                    url="https://linear.app/acme/issue/ENG-1#comment-c1",
                    created_at="2026-06-07T08:00:00Z",
                    updated_at="2026-06-07T09:00:00Z",
                )
            ],
        )

        self.assertIn("Address new Linear issue feedback", prompt)
        self.assertIn("Please cover the retry edge case.", prompt)
        self.assertIn("Existing branch: `codex/eng-1-ship-it`", prompt)
        self.assertIn("Do not commit, push, create pull requests, move Linear issues, or comment on Linear", prompt)

    def test_linear_comment_filter_ignores_orchestrator_content(self) -> None:
        human = LinearCommentFeedback(
            key="linear-comment:human:2026-06-07T09:00:00Z",
            id="human",
            author="reviewer",
            body="Please add a regression test.",
            url="https://linear.app/acme/issue/ENG-1#comment-human",
            created_at="2026-06-07T09:00:00Z",
            updated_at="2026-06-07T09:00:00Z",
        )
        status = LinearCommentFeedback(
            key="linear-comment:status:2026-06-07T08:00:00Z",
            id="status",
            author="aleix",
            body="Codex plan:\n\nDo the work.",
            url="https://linear.app/acme/issue/ENG-1#comment-status",
            created_at="2026-06-07T08:00:00Z",
            updated_at="2026-06-07T08:00:00Z",
        )
        marked = LinearCommentFeedback(
            key="linear-comment:marked:2026-06-07T08:30:00Z",
            id="marked",
            author="aleix",
            body=mark_linear_orchestrator_comment("Custom progress update."),
            url="https://linear.app/acme/issue/ENG-1#comment-marked",
            created_at="2026-06-07T08:30:00Z",
            updated_at="2026-06-07T08:30:00Z",
        )

        self.assertTrue(is_orchestrator_linear_comment(status.body))
        self.assertTrue(is_orchestrator_linear_comment(marked.body))
        self.assertEqual(actionable_linear_feedback([status, marked, human], set()), [human])
        self.assertEqual(actionable_linear_feedback([human], {human.key}), [])

    def test_linear_comment_filter_keeps_human_package_mentions(self) -> None:
        comment = LinearCommentFeedback(
            key="linear-comment:human:2026-06-07T09:00:00Z",
            id="human",
            author="reviewer",
            body="Please update the linear-codex-orchestrator package docs.",
            url="https://linear.app/acme/issue/ENG-1#comment-human",
            created_at="2026-06-07T09:00:00Z",
            updated_at="2026-06-07T09:00:00Z",
        )
        marked = LinearCommentFeedback(
            key="linear-comment:marked:2026-06-07T09:01:00Z",
            id="marked",
            author="codex",
            body="linear-codex-orchestrator\n\nCodex status.",
            url="https://linear.app/acme/issue/ENG-1#comment-marked",
            created_at="2026-06-07T09:01:00Z",
            updated_at="2026-06-07T09:01:00Z",
        )

        self.assertFalse(is_orchestrator_linear_comment(comment.body))
        self.assertTrue(is_orchestrator_linear_comment(marked.body))
        self.assertEqual(actionable_linear_feedback([comment, marked], set()), [comment])

    def test_linear_comment_filter_keeps_human_status_like_feedback(self) -> None:
        addressed = LinearCommentFeedback(
            key="linear-comment:addressed:2026-06-07T09:00:00Z",
            id="addressed",
            author="reviewer",
            body="Codex addressed the wrong field; please update the retry path instead.",
            url="https://linear.app/acme/issue/ENG-1#comment-addressed",
            created_at="2026-06-07T09:00:00Z",
            updated_at="2026-06-07T09:00:00Z",
        )
        reviewer = LinearCommentFeedback(
            key="linear-comment:reviewer:2026-06-07T09:01:00Z",
            id="reviewer",
            author="reviewer",
            body="Codex reviewer missed the error branch; add a regression test.",
            url="https://linear.app/acme/issue/ENG-1#comment-reviewer",
            created_at="2026-06-07T09:01:00Z",
            updated_at="2026-06-07T09:01:00Z",
        )
        legacy_status = LinearCommentFeedback(
            key="linear-comment:legacy:2026-06-07T08:00:00Z",
            id="legacy",
            author="codex",
            body="Codex addressed new Linear feedback.\n\nDone.",
            url="https://linear.app/acme/issue/ENG-1#comment-legacy",
            created_at="2026-06-07T08:00:00Z",
            updated_at="2026-06-07T08:00:00Z",
        )

        self.assertFalse(is_orchestrator_linear_comment(addressed.body))
        self.assertFalse(is_orchestrator_linear_comment(reviewer.body))
        self.assertTrue(is_orchestrator_linear_comment(legacy_status.body))
        self.assertEqual(
            actionable_linear_feedback([addressed, reviewer, legacy_status], set()),
            [addressed, reviewer],
        )

    def test_linear_comment_from_node_uses_id_and_updated_timestamp_key(self) -> None:
        comment = linear_comment_from_node(
            {
                "id": "comment-id",
                "body": "Looks good except one test.",
                "url": "https://linear.app/acme/issue/ENG-1#comment-id",
                "createdAt": "2026-06-07T08:00:00Z",
                "updatedAt": "2026-06-07T09:00:00Z",
                "user": {"displayName": "Reviewer", "name": "reviewer"},
            },
            "https://linear.app/acme/issue/ENG-1",
        )

        self.assertEqual(comment.key, "linear-comment:comment-id:2026-06-07T09:00:00Z")
        self.assertEqual(comment.author, "Reviewer")

    def test_seed_linear_feedback_state_records_existing_comments(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Review"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        human = LinearCommentFeedback(
            key="linear-comment:human:2026-06-07T09:00:00Z",
            id="human",
            author="reviewer",
            body="Clarification from before review.",
            url="https://linear.app/acme/issue/ENG-1#comment-human",
            created_at="2026-06-07T09:00:00Z",
            updated_at="2026-06-07T09:00:00Z",
        )
        orchestrator_comment = LinearCommentFeedback(
            key="linear-comment:codex:2026-06-07T10:00:00Z",
            id="codex",
            author="codex",
            body=mark_linear_orchestrator_comment("Codex status."),
            url="https://linear.app/acme/issue/ENG-1#comment-codex",
            created_at="2026-06-07T10:00:00Z",
            updated_at="2026-06-07T10:00:00Z",
        )

        class FakeLinear:
            async def issue_comments(self, _issue: object) -> list[LinearCommentFeedback]:
                return [human, orchestrator_comment]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = WorkspaceConfig(
                path=tmp_path / "workspace",
                repos={"web": RepoConfig("acme/web", tmp_path / "workspace" / "web", "develop")},
            )
            lock_dir = tmp_path / "locks"
            status = tmp_path / "status.json"
            orchestrator = Orchestrator(
                Settings(workspace_map={"ENG": workspace}, lock_dir=lock_dir),
                linear=FakeLinear(),
                github=object(),
            )
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=status):
                asyncio.run(orchestrator.seed_linear_feedback_state(issue))

            processed = read_processed_feedback(linear_feedback_state(lock_dir, issue.identifier))

        self.assertEqual(processed, {human.key})

    def test_pr_completion_continues_when_linear_feedback_seed_fails(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Progress"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": [{"name": "agent-running"}]},
            }
        )
        run_state = IssueRunState(
            issue_id="abc",
            issue_identifier="ENG-1",
            workspace_path="/tmp/workspace",
            branch="codex/eng-1-ship-it",
            stage="optimized",
            plan="saved plan",
            implementation_summary="saved summary",
        )

        class FakeLinear:
            def __init__(self) -> None:
                self.moves: list[tuple[str, str]] = []
                self.removed_labels: list[tuple[str, str]] = []

            async def issue_context(self, _issue: object) -> str:
                return "context"

            async def comment(self, _issue_id: str, _body: str) -> None:
                return None

            async def attach_pr(self, _issue_id: str, _url: str) -> None:
                return None

            async def move_issue(self, issue_id: str, status_name: str) -> None:
                self.moves.append((issue_id, status_name))

            async def remove_label(self, issue_id: str, label_name: str) -> None:
                self.removed_labels.append((issue_id, label_name))

        class FakeGitHub:
            async def create_or_update_pr(
                self,
                repo: str,
                _branch: str,
                _base: str,
                title: str,
                _body: str,
            ) -> PullRequest:
                return PullRequest(12, f"https://github.com/{repo}/pull/12", title)

        workspace = WorkspaceConfig(
            path=Path("/tmp/workspace"),
            repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
        )
        linear = FakeLinear()
        orchestrator = Orchestrator(
            Settings(workspace_map={"ENG": workspace}),
            linear=linear,
            github=FakeGitHub(),
        )
        orchestrator.changed_repos = (  # type: ignore[method-assign]
            lambda _workspace: workspace.repos
        )

        async def fake_review(*_args: object) -> ReviewResult:
            return ReviewResult(True, "Review passed", "tests passed")

        orchestrator._review = fake_review  # type: ignore[method-assign]

        with tempfile.TemporaryDirectory() as tmp:
            status_file = Path(tmp) / "status.json"
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=status_file):
                with patch.object(orchestrator, "checkout_existing_branch"):
                    with patch.object(
                        orchestrator,
                        "seed_linear_feedback_state",
                        side_effect=RuntimeError("Linear outage"),
                    ):
                        with patch(
                            "linear_codex_orchestrator.orchestrator.read_issue_run_state",
                            return_value=run_state,
                        ):
                            with patch(
                                "linear_codex_orchestrator.orchestrator.write_issue_run_state"
                            ):
                                with patch(
                                    "linear_codex_orchestrator.orchestrator.clear_issue_run_state"
                                ):
                                    with patch(
                                        "linear_codex_orchestrator.orchestrator.has_changes",
                                        return_value=False,
                                    ):
                                        with patch(
                                            "linear_codex_orchestrator.orchestrator.push_branch"
                                        ):
                                            asyncio.run(
                                                orchestrator._process_locked_issue(
                                                    issue,
                                                    workspace,
                                                    resume=True,
                                                )
                                            )

        self.assertEqual(linear.moves, [("abc", "In Review")])
        self.assertEqual(linear.removed_labels, [("abc", "agent-running")])

    def test_process_linear_feedback_uses_workspace_lock(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Review"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )

        class FakeLinear:
            calls = 0

            async def issue_comments(self, _issue: object) -> list[LinearCommentFeedback]:
                self.calls += 1
                return []

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = WorkspaceConfig(
                path=tmp_path / "workspace",
                repos={"web": RepoConfig("acme/web", tmp_path / "workspace" / "web", "develop")},
            )
            lock_dir = tmp_path / "locks"
            linear = FakeLinear()
            orchestrator = Orchestrator(
                Settings(workspace_map={"ENG": workspace}, lock_dir=lock_dir),
                linear=linear,
                github=object(),
            )
            with lock_for_repo(lock_dir, f"{issue.team_key}:{workspace.path}"):
                with patch.object(orchestrator, "linear_feedback_branch_available", return_value=True):
                    asyncio.run(orchestrator.process_linear_feedback(issue))

        self.assertEqual(linear.calls, 0)

    def test_linear_feedback_candidates_only_queries_in_review_issues(self) -> None:
        in_review_issue = parse_linear_issue(
            {
                "id": "review",
                "identifier": "ENG-1",
                "title": "Review me",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Review"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )

        class FakeLinear:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str | None, int, tuple[str, ...]]] = []

            async def ready_issues(
                self,
                status: str,
                label: str | None,
                limit: int,
                excluded_labels: tuple[str, ...],
                _team_keys: tuple[str, ...],
            ) -> list[LinearIssue]:
                self.calls.append((status, label, limit, excluded_labels))
                return [in_review_issue]

        workspace = WorkspaceConfig(
            path=Path("/tmp/workspace"),
            repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
        )
        linear = FakeLinear()
        orchestrator = Orchestrator(
            Settings(workspace_map={"ENG": workspace}),
            linear=linear,
            github=object(),
        )

        candidates = asyncio.run(orchestrator.linear_feedback_candidates())

        self.assertEqual(candidates, [in_review_issue])
        self.assertEqual(
            linear.calls,
            [("In Review", None, 10, ("agent-running", "agent-blocked"))],
        )

    def test_run_linear_feedback_once_caps_processed_feedback_passes(self) -> None:
        issues = [
            parse_linear_issue(
                {
                    "id": f"issue-{index}",
                    "identifier": f"ENG-{index}",
                    "title": f"Review me {index}",
                    "description": "",
                    "url": f"https://linear.app/acme/issue/ENG-{index}",
                    "state": {"name": "In Review"},
                    "team": {"key": "ENG", "name": "Engineering"},
                    "labels": {"nodes": []},
                }
            )
            for index in range(1, 4)
        ]

        workspace = WorkspaceConfig(
            path=Path("/tmp/workspace"),
            repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
        )
        orchestrator = Orchestrator(
            Settings(workspace_map={"ENG": workspace}, max_issues_per_tick=1),
            linear=object(),
            github=object(),
        )
        calls: list[str] = []

        async def fake_candidates() -> list[LinearIssue]:
            return issues

        async def fake_process(issue: LinearIssue) -> bool:
            calls.append(issue.identifier)
            return issue.identifier != "ENG-1"

        orchestrator.linear_feedback_candidates = fake_candidates  # type: ignore[method-assign]
        orchestrator.process_linear_feedback = fake_process  # type: ignore[method-assign]

        asyncio.run(orchestrator.run_linear_feedback_once())

        self.assertEqual(calls, ["ENG-1", "ENG-2"])

    def test_update_issue_linear_feedback_status_populates_empty_issue_metadata(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Review"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
                "project": {"name": "Project X", "url": "https://linear.app/acme/project/x"},
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / "status.json"
            status.write_text('{"issues": {"ENG-1": {}}}\n', encoding="utf-8")
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=status):
                update_issue_linear_feedback_status(issue, "Linear feedback found", 1)
                payload = read_status()

        current = payload["issues"]["ENG-1"]
        self.assertEqual(current["identifier"], "ENG-1")
        self.assertEqual(current["title"], "Ship it")
        self.assertEqual(current["url"], "https://linear.app/acme/issue/ENG-1")
        self.assertEqual(current["team"], "ENG")
        self.assertEqual(current["project"], "Project X")
        self.assertEqual(current["project_url"], "https://linear.app/acme/project/x")
        self.assertEqual(current["linear_feedback"], "Linear feedback found (1 comment)")

    def test_pull_request_number_is_parsed_from_created_pr_url(self) -> None:
        self.assertEqual(pull_request_number_from_url("https://github.com/acme/web/pull/42"), 42)
        self.assertEqual(pull_request_number_from_url("https://github.com/acme/web/pull/42#discussion"), 42)
        self.assertEqual(pull_request_number_from_url("https://github.com/acme/web/compare/main...branch"), 0)

    def test_codex_approval_reviews_require_approved_state_and_thumb(self) -> None:
        approved = {
            "id": 1,
            "state": "APPROVED",
            "body": "Looks good 👍",
            "submitted_at": "2026-06-07T08:00:00Z",
            "html_url": "https://github.com/acme/web/pull/1#pullrequestreview-1",
            "user": {"login": "codex"},
        }
        approved_without_thumb = {
            "id": 2,
            "state": "APPROVED",
            "body": "Looks good",
            "submitted_at": "2026-06-07T08:01:00Z",
        }
        commented_with_thumb = {
            "id": 3,
            "state": "COMMENTED",
            "body": "Needs work 👍",
            "submitted_at": "2026-06-07T08:02:00Z",
            "user": {"login": "codex"},
        }
        human_approved_with_thumb = {
            "id": 4,
            "state": "APPROVED",
            "body": "Looks good 👍",
            "submitted_at": "2026-06-07T08:03:00Z",
            "user": {"login": "human-reviewer"},
        }

        self.assertTrue(is_codex_approval_review(approved))
        self.assertFalse(is_codex_approval_review(approved_without_thumb))
        self.assertFalse(is_codex_approval_review(commented_with_thumb))
        self.assertFalse(is_codex_approval_review(human_approved_with_thumb))
        approvals = codex_approval_reviews(
            [approved, approved_without_thumb, commented_with_thumb, human_approved_with_thumb]
        )

        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0].author, "codex")
        self.assertEqual(approvals[0].submitted_at, "2026-06-07T08:00:00Z")

    def test_parse_gh_api_json_flattens_paginated_arrays(self) -> None:
        raw = (
            '[{"id":1,"state":"COMMENTED"}]\n'
            '[{"id":2,"state":"APPROVED"},{"id":3,"state":"APPROVED"}]\n'
        )

        payload = parse_gh_api_json(raw, paginate=True)

        self.assertEqual([item["id"] for item in payload], [1, 2, 3])

    def test_pr_codex_approvals_uses_paginated_reviews_endpoint(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command: list[str]) -> str:
            commands.append(command)
            return (
                '[{"id":1,"state":"COMMENTED","body":"","user":{"login":"codex"}}]\n'
                '[{"id":2,"state":"APPROVED","body":"👍","submitted_at":"2026-06-07T08:00:00Z",'
                '"commit_id":"head-sha","html_url":"https://github.com/acme/web/pull/1#pullrequestreview-2",'
                '"user":{"login":"codex"}}]\n'
            )

        with patch("linear_codex_orchestrator.local_github_client._run", fake_run):
            approvals = asyncio.run(LocalGitHubClient().pr_codex_approvals("acme/web", 1))

        self.assertEqual(commands, [["gh", "api", "--paginate", "repos/acme/web/pulls/1/reviews?per_page=100"]])
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0].commit_id, "head-sha")

    def test_latest_codex_approval_requires_current_head_commit(self) -> None:
        stale = PullRequestApproval(
            key="review:1:2026-06-07T08:00:00Z",
            author="codex",
            submitted_at="2026-06-07T08:00:00Z",
            url="https://github.com/acme/web/pull/12#pullrequestreview-1",
            body="👍",
            commit_id="old-sha",
        )
        current = PullRequestApproval(
            key="review:2:2026-06-07T09:00:00Z",
            author="codex",
            submitted_at="2026-06-07T09:00:00Z",
            url="https://github.com/acme/web/pull/12#pullrequestreview-2",
            body="👍",
            commit_id="head-sha",
        )

        class FakeGitHub:
            async def pr_codex_approvals(
                self,
                _repo: str,
                _number: int,
            ) -> list[PullRequestApproval]:
                return [stale, current]

        approval = asyncio.run(latest_codex_approval(FakeGitHub(), "acme/web", 12, "head-sha"))
        missing_head_approval = asyncio.run(latest_codex_approval(FakeGitHub(), "acme/web", 12, ""))
        stale_only_approval = asyncio.run(latest_codex_approval(FakeGitHub(), "acme/web", 12, "other-sha"))

        self.assertEqual(approval, current)
        self.assertIsNone(missing_head_approval)
        self.assertIsNone(stale_only_approval)

    def test_pr_feedback_state_round_trips_processed_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            self.assertEqual(read_processed_feedback(path), set())
            write_processed_feedback(path, {"b", "a"})
            self.assertEqual(read_processed_feedback(path), {"a", "b"})

    def test_processed_feedback_state_ignores_invalid_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"processed": "not-a-list"}', encoding="utf-8")
            self.assertEqual(read_processed_feedback(path), set())

            path.write_text('{"processed": ["a", 2, "b"]}', encoding="utf-8")
            self.assertEqual(read_processed_feedback(path), {"a", "b"})

    def test_codex_log_path_is_stable_and_sanitized(self) -> None:
        path = codex_log_path("ENG/20", "review fix")
        self.assertEqual(path.parent, Path(".logs"))
        self.assertRegex(path.name, r"^\d{8}-\d{6}-eng-20-review-fix\.log$")

    def test_session_start_marker_is_appended_to_orchestration_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "orchestrator.log"
            with patch("linear_codex_orchestrator.orchestrator.orchestration_log_path", return_value=log_path):
                log_session_start()
            text = log_path.read_text(encoding="utf-8")
            self.assertIn("===== New session started", text)

    def test_status_file_tracks_issues_and_prs(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "Todo"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        pr = OpenPullRequest(
            repo="acme/web",
            number=12,
            url="https://github.com/acme/web/pull/12",
            title="ENG-1: Ship it",
            head_branch="codex/eng-1",
            base_branch="develop",
            head_sha="abc123",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=path):
                update_issue_status(
                    issue,
                    "Reviewing",
                    **workspace_status_context(
                        WorkspaceConfig(
                            path=Path("/tmp/workspace"),
                            repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
                        )
                    ),
                )
                update_issue_status(issue, "Implemented", changed_repos="web")
                update_pr_status(pr, "Ready for review", issue="ENG-1", repo_key="web", repo_path=Path("/tmp/workspace/web"))
                status = read_status()
        self.assertEqual(status["issues"]["ENG-1"]["status"], "Implemented")
        self.assertEqual(status["issues"]["ENG-1"]["workspace_path"], "/tmp/workspace")
        self.assertEqual(status["issues"]["ENG-1"]["repos"][0]["path"], "/tmp/workspace/web")
        self.assertEqual(status["issues"]["ENG-1"]["changed_repos"], "web")
        self.assertEqual(status["prs"]["acme/web#12"]["status"], "Ready for review")
        self.assertEqual(status["prs"]["acme/web#12"]["issue"], "ENG-1")
        self.assertEqual(status["prs"]["acme/web#12"]["repo_path"], "/tmp/workspace/web")
        self.assertEqual(status["archived_prs"], {})

    def test_pr_feedback_status_is_associated_with_existing_issue(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Review"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        pr = OpenPullRequest(
            repo="acme/web",
            number=12,
            url="https://github.com/acme/web/pull/12",
            title="ENG-1: Ship it",
            head_branch="codex/eng-1-ship-it",
            base_branch="develop",
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            path = tmp_path / "status.json"
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=path):
                with patch("linear_codex_orchestrator.web_server.LOG_DIR", tmp_path):
                    update_issue_status(issue, "PR ready")
                    issue_identifier = pr_feedback_issue_identifier(pr)
                    update_pr_feedback_status(
                        pr,
                        "Feedback addressed",
                        issue=issue_identifier,
                        repo_key="web",
                        repo_path=Path("/tmp/workspace/web"),
                        feedback_count=2,
                    )
                    status = read_status()
                    summary = status_index()

        self.assertEqual(issue_identifier, "ENG-1")
        self.assertEqual(status["prs"]["acme/web#12"]["issue"], "ENG-1")
        self.assertEqual(status["issues"]["ENG-1"]["prs"], "https://github.com/acme/web/pull/12")
        self.assertEqual(status["issues"]["ENG-1"]["pr_feedback"], "acme/web#12: Feedback addressed (2 items)")
        self.assertEqual(summary["prs"], [])
        self.assertEqual(summary["issues"][0]["pr_feedback"], "acme/web#12: Feedback addressed (2 items)")

    def test_pr_feedback_status_without_existing_issue_stays_orphaned(self) -> None:
        pr = OpenPullRequest(
            repo="acme/web",
            number=12,
            url="https://github.com/acme/web/pull/12",
            title="ENG-1: Ship it",
            head_branch="codex/eng-1-ship-it",
            base_branch="develop",
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            path = tmp_path / "status.json"
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=path):
                with patch("linear_codex_orchestrator.web_server.LOG_DIR", tmp_path):
                    issue_identifier = pr_feedback_issue_identifier(pr)
                    update_pr_feedback_status(
                        pr,
                        "Feedback found",
                        issue=issue_identifier,
                        repo_key="web",
                        repo_path=Path("/tmp/workspace/web"),
                        feedback_count=1,
                    )
                    summary = status_index()

        self.assertIsNone(issue_identifier)
        self.assertEqual(summary["prs"][0]["key"], "acme/web#12")

    def test_update_issue_codex_approval_preserves_issue_status(self) -> None:
        pr = OpenPullRequest(
            repo="acme/web",
            number=12,
            url="https://github.com/acme/web/pull/12",
            title="ENG-1: Ship it",
            head_branch="codex/eng-1",
            base_branch="develop",
        )
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "Todo"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        approval = PullRequestApproval(
            key="review:1:2026-06-07T08:00:00Z",
            author="codex",
            submitted_at="2026-06-07T08:00:00Z",
            url="https://github.com/acme/web/pull/12#pullrequestreview-1",
            body="👍",
            commit_id="abc123",
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=path):
                update_issue_status(issue, "PR ready")
                self.assertTrue(update_issue_codex_approval("ENG-1", pr, approval))
                self.assertFalse(update_issue_codex_approval("ENG-1", pr, approval))
                status = read_status()

        issue_status = status["issues"]["ENG-1"]
        self.assertEqual(issue_status["status"], "PR ready")
        self.assertTrue(issue_status["codex_approved"])
        self.assertEqual(issue_status["codex_approved_at"], "2026-06-07T08:00:00Z")
        self.assertEqual(issue_status["codex_approval_url"], approval.url)
        self.assertEqual(issue_status["codex_approved_pr"], pr.url)

    def test_clear_issue_codex_approval_only_clears_matching_pr(self) -> None:
        pr = OpenPullRequest(
            repo="acme/web",
            number=12,
            url="https://github.com/acme/web/pull/12",
            title="ENG-1: Ship it",
            head_branch="codex/eng-1",
            base_branch="develop",
            head_sha="abc123",
        )
        approval = PullRequestApproval(
            key="review:1:2026-06-07T08:00:00Z",
            author="codex",
            submitted_at="2026-06-07T08:00:00Z",
            url="https://github.com/acme/web/pull/12#pullrequestreview-1",
            body="👍",
            commit_id="abc123",
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=path):
                self.assertFalse(clear_issue_codex_approval("ENG-1", pr))
                update_issue_codex_approval("ENG-1", pr, approval)
                self.assertTrue(clear_issue_codex_approval("ENG-1", pr))
                status = read_status()

        issue_status = status["issues"]["ENG-1"]
        self.assertEqual(issue_status["status"], "PR ready")
        self.assertNotIn("codex_approved", issue_status)
        self.assertNotIn("codex_approval_url", issue_status)

    def test_process_pr_feedback_maps_codex_approval_to_issue_status(self) -> None:
        pr = OpenPullRequest(
            repo="acme/web",
            number=12,
            url="https://github.com/acme/web/pull/12",
            title="ENG-1: Ship it",
            head_branch="codex/eng-1",
            base_branch="develop",
            head_sha="abc123",
        )
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "Todo"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        approval = PullRequestApproval(
            key="review:1:2026-06-07T08:00:00Z",
            author="codex",
            submitted_at="2026-06-07T08:00:00Z",
            url="https://github.com/acme/web/pull/12#pullrequestreview-1",
            body="👍",
            commit_id="abc123",
        )

        class FakeGitHub:
            async def pr_codex_approvals(
                self,
                _repo: str,
                _number: int,
            ) -> list[PullRequestApproval]:
                return [approval]

            async def pr_feedback(self, _repo: str, _number: int) -> list[PullRequestFeedback]:
                return []

        repo = RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")
        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / "status.json"
            lock_dir = Path(tmp) / "locks"
            orchestrator = Orchestrator(
                Settings(workspace_map={}, lock_dir=lock_dir),
                github=FakeGitHub(),
            )
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=status):
                update_issue_status(issue, "PR ready")
                update_pr_status(pr, "Ready for review", issue="ENG-1")
                asyncio.run(orchestrator.process_pr_feedback("web", repo, pr))
                payload = read_status()

        self.assertTrue(payload["issues"]["ENG-1"]["codex_approved"])
        self.assertEqual(payload["issues"]["ENG-1"]["codex_approved_pr"], pr.url)
        self.assertEqual(payload["prs"]["acme/web#12"]["issue"], "ENG-1")

    def test_process_pr_feedback_persists_inferred_issue_for_codex_approval(self) -> None:
        pr = OpenPullRequest(
            repo="acme/web",
            number=12,
            url="https://github.com/acme/web/pull/12",
            title="ENG-1: Ship it",
            head_branch="codex/eng-1",
            base_branch="develop",
            head_sha="abc123",
        )
        approval = PullRequestApproval(
            key="review:1:2026-06-07T08:00:00Z",
            author="codex",
            submitted_at="2026-06-07T08:00:00Z",
            url="https://github.com/acme/web/pull/12#pullrequestreview-1",
            body="👍",
            commit_id="abc123",
        )

        class FakeGitHub:
            async def pr_codex_approvals(
                self,
                _repo: str,
                _number: int,
            ) -> list[PullRequestApproval]:
                return [approval]

            async def pr_feedback(self, _repo: str, _number: int) -> list[PullRequestFeedback]:
                return []

        repo = RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")
        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / "status.json"
            lock_dir = Path(tmp) / "locks"
            orchestrator = Orchestrator(
                Settings(workspace_map={}, lock_dir=lock_dir),
                github=FakeGitHub(),
            )
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=status):
                asyncio.run(orchestrator.process_pr_feedback("web", repo, pr))
                payload = read_status()

        self.assertTrue(payload["issues"]["ENG-1"]["codex_approved"])
        self.assertEqual(payload["prs"]["acme/web#12"]["issue"], "ENG-1")

    def test_process_pr_feedback_keeps_inferred_pending_feedback_issue_visible(self) -> None:
        pr = OpenPullRequest(
            repo="acme/web",
            number=12,
            url="https://github.com/acme/web/pull/12",
            title="ENG-1: Ship it",
            head_branch="codex/eng-1",
            base_branch="develop",
            head_sha="new-sha",
        )
        stale_approval = PullRequestApproval(
            key="review:1:2026-06-07T08:00:00Z",
            author="codex",
            submitted_at="2026-06-07T08:00:00Z",
            url="https://github.com/acme/web/pull/12#pullrequestreview-1",
            body="👍",
            commit_id="old-sha",
        )

        class FakeGitHub:
            async def pr_codex_approvals(
                self,
                _repo: str,
                _number: int,
            ) -> list[PullRequestApproval]:
                return [stale_approval]

            async def pr_feedback(self, _repo: str, _number: int) -> list[PullRequestFeedback]:
                return [
                    PullRequestFeedback(
                        key="review-comment:1:2026-06-07T09:00:00Z",
                        kind="review comment",
                        author="reviewer",
                        body="Please update this.",
                        url="https://github.com/acme/web/pull/12#discussion_r1",
                    )
                ]

        repo = RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")
        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / "status.json"
            lock_dir = Path(tmp) / "locks"
            orchestrator = Orchestrator(
                Settings(workspace_map={}, lock_dir=lock_dir, dry_run=True),
                github=FakeGitHub(),
            )
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=status):
                asyncio.run(orchestrator.process_pr_feedback("web", repo, pr))
                payload = read_status()

        self.assertEqual(payload["prs"]["acme/web#12"]["issue"], "ENG-1")
        self.assertEqual(payload["prs"]["acme/web#12"]["status"], "Feedback found")
        self.assertEqual(payload["issues"]["ENG-1"]["status"], "PR feedback")
        self.assertEqual(payload["issues"]["ENG-1"]["pr_feedback"], "acme/web#12: Feedback found (1 item)")
        self.assertNotIn("codex_approved", payload["prs"]["acme/web#12"])

    def test_process_pr_feedback_clears_stale_codex_approval_when_review_is_missing(self) -> None:
        pr = OpenPullRequest(
            repo="acme/web",
            number=12,
            url="https://github.com/acme/web/pull/12",
            title="ENG-1: Ship it",
            head_branch="codex/eng-1",
            base_branch="develop",
        )
        approval = PullRequestApproval(
            key="review:1:2026-06-07T08:00:00Z",
            author="codex",
            submitted_at="2026-06-07T08:00:00Z",
            url="https://github.com/acme/web/pull/12#pullrequestreview-1",
            body="👍",
        )

        class FakeGitHub:
            async def pr_codex_approvals(
                self,
                _repo: str,
                _number: int,
            ) -> list[PullRequestApproval]:
                return []

            async def pr_feedback(self, _repo: str, _number: int) -> list[PullRequestFeedback]:
                return []

        repo = RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")
        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / "status.json"
            lock_dir = Path(tmp) / "locks"
            orchestrator = Orchestrator(
                Settings(workspace_map={}, lock_dir=lock_dir),
                github=FakeGitHub(),
            )
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=status):
                update_issue_codex_approval("ENG-1", pr, approval)
                update_pr_status(pr, "Codex approved", issue="ENG-1", codex_approval=approval)
                asyncio.run(orchestrator.process_pr_feedback("web", repo, pr))
                payload = read_status()

        self.assertNotIn("codex_approved", payload["issues"]["ENG-1"])
        pr_status = payload["prs"]["acme/web#12"]
        self.assertEqual(pr_status["status"], "No new feedback")
        self.assertNotIn("codex_approved", pr_status)
        self.assertEqual(pr_status["issue"], "ENG-1")

    def test_process_pr_feedback_defers_approval_while_feedback_is_pending(self) -> None:
        pr = OpenPullRequest(
            repo="acme/web",
            number=12,
            url="https://github.com/acme/web/pull/12",
            title="ENG-1: Ship it",
            head_branch="codex/eng-1",
            base_branch="develop",
        )
        approval = PullRequestApproval(
            key="review:1:2026-06-07T08:00:00Z",
            author="codex",
            submitted_at="2026-06-07T08:00:00Z",
            url="https://github.com/acme/web/pull/12#pullrequestreview-1",
            body="👍",
        )

        class FakeGitHub:
            async def pr_codex_approvals(
                self,
                _repo: str,
                _number: int,
            ) -> list[PullRequestApproval]:
                return [approval]

            async def pr_feedback(self, _repo: str, _number: int) -> list[PullRequestFeedback]:
                return [
                    PullRequestFeedback(
                        key="review-comment:1:2026-06-07T09:00:00Z",
                        kind="review comment",
                        author="reviewer",
                        body="Please update this.",
                        url="https://github.com/acme/web/pull/12#discussion_r1",
                    )
                ]

        repo = RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")
        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / "status.json"
            lock_dir = Path(tmp) / "locks"
            orchestrator = Orchestrator(
                Settings(workspace_map={}, lock_dir=lock_dir, dry_run=True),
                github=FakeGitHub(),
            )
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=status):
                update_issue_codex_approval("ENG-1", pr, approval)
                update_pr_status(pr, "Codex approved", issue="ENG-1", codex_approval=approval)
                asyncio.run(orchestrator.process_pr_feedback("web", repo, pr))
                payload = read_status()

        self.assertNotIn("codex_approved", payload["issues"]["ENG-1"])
        pr_status = payload["prs"]["acme/web#12"]
        self.assertEqual(pr_status["status"], "Feedback found")
        self.assertNotIn("codex_approved", pr_status)
        self.assertEqual(pr_status["issue"], "ENG-1")

    def test_process_pr_feedback_keeps_unmapped_codex_approval_in_pr_status(self) -> None:
        pr = OpenPullRequest(
            repo="acme/web",
            number=12,
            url="https://github.com/acme/web/pull/12",
            title="Ship it",
            head_branch="codex/ship-it",
            base_branch="develop",
            head_sha="abc123",
        )
        approval = PullRequestApproval(
            key="review:1:2026-06-07T08:00:00Z",
            author="codex",
            submitted_at="2026-06-07T08:00:00Z",
            url="https://github.com/acme/web/pull/12#pullrequestreview-1",
            body="👍",
            commit_id="abc123",
        )

        class FakeGitHub:
            async def pr_codex_approvals(
                self,
                _repo: str,
                _number: int,
            ) -> list[PullRequestApproval]:
                return [approval]

            async def pr_feedback(self, _repo: str, _number: int) -> list[PullRequestFeedback]:
                return []

        repo = RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")
        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / "status.json"
            lock_dir = Path(tmp) / "locks"
            orchestrator = Orchestrator(
                Settings(workspace_map={}, lock_dir=lock_dir),
                github=FakeGitHub(),
            )
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=status):
                asyncio.run(orchestrator.process_pr_feedback("web", repo, pr))
                payload = read_status()

        pr_status = payload["prs"]["acme/web#12"]
        self.assertEqual(pr_status["status"], "Codex approved")
        self.assertTrue(pr_status["codex_approved"])
        self.assertEqual(pr_status["codex_approved_pr"], pr.url)
        self.assertNotIn("issue", pr_status)

    def test_process_linear_feedback_dry_run_detects_without_persisting(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Review"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        feedback = LinearCommentFeedback(
            key="linear-comment:c1:2026-06-07T09:00:00Z",
            id="c1",
            author="reviewer",
            body="Please add an edge-case test.",
            url="https://linear.app/acme/issue/ENG-1#comment-c1",
            created_at="2026-06-07T09:00:00Z",
            updated_at="2026-06-07T09:00:00Z",
        )

        class FakeLinear:
            async def issue_comments(self, _issue: object) -> list[LinearCommentFeedback]:
                return [feedback]

        workspace = WorkspaceConfig(
            path=Path("/tmp/workspace"),
            repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            status = tmp_path / "status.json"
            settings = Settings(workspace_map={"ENG": workspace}, lock_dir=tmp_path / "locks", dry_run=True)
            orchestrator = Orchestrator(settings, linear=FakeLinear(), github=object())
            state_path = linear_feedback_state(settings.lock_dir, "ENG-1")
            write_processed_feedback(state_path, set())
            with patch.object(orchestrator, "linear_feedback_branch_available", return_value=True):
                with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=status):
                    asyncio.run(orchestrator.process_linear_feedback(issue))
                    payload = read_status()

            self.assertEqual(read_processed_feedback(state_path), set())

        self.assertEqual(payload["issues"]["ENG-1"]["linear_feedback"], "Linear feedback found (1 comment)")
        self.assertEqual(payload["issues"]["ENG-1"]["linear_feedback_count"], 1)

    def test_process_linear_feedback_skips_dirty_workspace_without_persisting(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Review"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        feedback = LinearCommentFeedback(
            key="linear-comment:c1:2026-06-07T09:00:00Z",
            id="c1",
            author="reviewer",
            body="Please add an edge-case test.",
            url="https://linear.app/acme/issue/ENG-1#comment-c1",
            created_at="2026-06-07T09:00:00Z",
            updated_at="2026-06-07T09:00:00Z",
        )

        class FakeLinear:
            def __init__(self) -> None:
                self.context_calls = 0

            async def issue_comments(self, _issue: object) -> list[LinearCommentFeedback]:
                return [feedback]

            async def issue_context(self, _issue: object) -> str:
                self.context_calls += 1
                return "# ENG-1: Ship it"

        workspace = WorkspaceConfig(
            path=Path("/tmp/workspace"),
            repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
        )
        linear = FakeLinear()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            status = tmp_path / "status.json"
            settings = Settings(workspace_map={"ENG": workspace}, lock_dir=tmp_path / "locks")
            orchestrator = Orchestrator(settings, linear=linear, github=object())
            state_path = linear_feedback_state(settings.lock_dir, "ENG-1")
            write_processed_feedback(state_path, set())
            with patch.object(orchestrator, "linear_feedback_branch_available", return_value=True):
                with patch.object(orchestrator, "dirty_workspace_repos", return_value=["web"]):
                    with patch.object(orchestrator, "checkout_existing_branch_from_origin") as checkout:
                        with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=status):
                            asyncio.run(orchestrator.process_linear_feedback(issue))
                            payload = read_status()

            self.assertEqual(read_processed_feedback(state_path), set())

        checkout.assert_not_called()
        self.assertEqual(linear.context_calls, 0)
        self.assertEqual(payload["issues"]["ENG-1"]["linear_feedback"], "Workspace dirty (1 comment)")

    def test_process_linear_feedback_normal_flow_commits_updates_pr_and_persists(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Review"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        feedback = LinearCommentFeedback(
            key="linear-comment:c1:2026-06-07T09:00:00Z",
            id="c1",
            author="reviewer",
            body="Please add an edge-case test.",
            url="https://linear.app/acme/issue/ENG-1#comment-c1",
            created_at="2026-06-07T09:00:00Z",
            updated_at="2026-06-07T09:00:00Z",
        )

        class FakeLinear:
            def __init__(self) -> None:
                self.comments: list[str] = []

            async def issue_comments(self, _issue: object) -> list[LinearCommentFeedback]:
                return [feedback]

            async def issue_context(self, _issue: object) -> str:
                return "# ENG-1: Ship it"

            async def comment(self, _issue_id: str, body: str) -> None:
                self.comments.append(body)

        class FakeGitHub:
            async def create_or_update_pr(
                self,
                repo: str,
                _branch: str,
                _base: str,
                title: str,
                _body: str,
            ) -> PullRequest:
                return PullRequest(12, f"https://github.com/{repo}/pull/12", title)

        class TestOrchestrator(Orchestrator):
            def linear_feedback_branch_available(self, _workspace: WorkspaceConfig, _branch: str) -> bool:
                return True

            def dirty_workspace_repos(self, _workspace: WorkspaceConfig) -> list[str]:
                return []

            def checkout_existing_branch_from_origin(
                self,
                _workspace: WorkspaceConfig,
                _branch: str,
            ) -> None:
                return None

            def changed_repos(
                self,
                workspace_arg: WorkspaceConfig,
            ) -> dict[str, RepoConfig]:
                return workspace_arg.repos

            async def _fix_linear_feedback(
                self,
                *_args: object,
                **_kwargs: object,
            ) -> str:
                return "Addressed Linear feedback."

        workspace = WorkspaceConfig(
            path=Path("/tmp/workspace"),
            repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
        )
        linear = FakeLinear()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            status = tmp_path / "status.json"
            settings = Settings(workspace_map={"ENG": workspace}, lock_dir=tmp_path / "locks")
            orchestrator = TestOrchestrator(settings, linear=linear, github=FakeGitHub())
            write_processed_feedback(linear_feedback_state(settings.lock_dir, "ENG-1"), set())
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=status):
                pr = OpenPullRequest(
                    repo="acme/web",
                    number=12,
                    url="https://github.com/acme/web/pull/12",
                    title="ENG-1: Ship it",
                    head_branch="codex/eng-1-ship-it",
                    base_branch="develop",
                )
                approval = PullRequestApproval(
                    key="review:1:2026-06-07T08:00:00Z",
                    author="codex",
                    submitted_at="2026-06-07T08:00:00Z",
                    url="https://github.com/acme/web/pull/12#pullrequestreview-1",
                    body="👍",
                    commit_id="abc123",
                )
                update_issue_codex_approval("ENG-1", pr, approval)
                update_pr_status(pr, "Codex approved", issue="ENG-1", codex_approval=approval)
                with patch("linear_codex_orchestrator.orchestrator.has_changes", return_value=True):
                    with patch("linear_codex_orchestrator.orchestrator.commit_all") as commit:
                        with patch("linear_codex_orchestrator.orchestrator.push_branch") as push:
                            with patch(
                                "linear_codex_orchestrator.orchestrator.run_git",
                                return_value="file.py | 1 +",
                            ):
                                asyncio.run(orchestrator.process_linear_feedback(issue))
                                payload = read_status()

            state_path = linear_feedback_state(settings.lock_dir, "ENG-1")
            self.assertEqual(read_processed_feedback(state_path), {feedback.key})

        commit.assert_called_once()
        push.assert_called_once()
        self.assertIn("Updated for Linear feedback", payload["prs"]["acme/web#12"]["status"])
        self.assertNotIn("codex_approved", payload["prs"]["acme/web#12"])
        self.assertNotIn("codex_approved", payload["issues"]["ENG-1"])
        self.assertEqual(payload["issues"]["ENG-1"]["linear_feedback"], "Linear feedback addressed (1 comment)")
        self.assertTrue(linear.comments)
        self.assertIn("linear-codex-orchestrator", linear.comments[0])

    def test_process_linear_feedback_pushes_committed_feedback_changes(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Review"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        feedback = LinearCommentFeedback(
            key="linear-comment:c1:2026-06-07T09:00:00Z",
            id="c1",
            author="reviewer",
            body="Please add an edge-case test.",
            url="https://linear.app/acme/issue/ENG-1#comment-c1",
            created_at="2026-06-07T09:00:00Z",
            updated_at="2026-06-07T09:00:00Z",
        )

        class FakeLinear:
            async def issue_comments(self, _issue: object) -> list[LinearCommentFeedback]:
                return [feedback]

            async def issue_context(self, _issue: object) -> str:
                return "# ENG-1: Ship it"

            async def comment(self, _issue_id: str, _body: str) -> None:
                return None

        class FakeGitHub:
            async def create_or_update_pr(
                self,
                repo: str,
                _branch: str,
                _base: str,
                title: str,
                _body: str,
            ) -> PullRequest:
                return PullRequest(12, f"https://github.com/{repo}/pull/12", title)

        class TestOrchestrator(Orchestrator):
            def linear_feedback_branch_available(self, _workspace: WorkspaceConfig, _branch: str) -> bool:
                return True

            def dirty_workspace_repos(self, _workspace: WorkspaceConfig) -> list[str]:
                return []

            def checkout_existing_branch_from_origin(self, _workspace: WorkspaceConfig, _branch: str) -> None:
                return None

            def changed_repos_since_heads(
                self,
                workspace_arg: WorkspaceConfig,
                _before_heads: dict[str, str],
            ) -> dict[str, RepoConfig]:
                return workspace_arg.repos

            async def _fix_linear_feedback(self, *_args: object, **_kwargs: object) -> str:
                return "Committed Linear feedback changes."

        workspace = WorkspaceConfig(
            path=Path("/tmp/workspace"),
            repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            settings = Settings(workspace_map={"ENG": workspace}, lock_dir=tmp_path / "locks")
            orchestrator = TestOrchestrator(settings, linear=FakeLinear(), github=FakeGitHub())
            write_processed_feedback(linear_feedback_state(settings.lock_dir, "ENG-1"), set())
            with patch(
                "linear_codex_orchestrator.orchestrator.status_path",
                return_value=tmp_path / "status.json",
            ):
                with patch("linear_codex_orchestrator.orchestrator.has_changes", return_value=False):
                    with patch("linear_codex_orchestrator.orchestrator.commit_all") as commit:
                        with patch("linear_codex_orchestrator.orchestrator.push_branch") as push:
                            with patch(
                                "linear_codex_orchestrator.orchestrator.run_git",
                                return_value="file.py | 1 +",
                            ):
                                asyncio.run(orchestrator.process_linear_feedback(issue))

        commit.assert_not_called()
        push.assert_called_once()

    def test_process_linear_feedback_noops_with_unchanged_branch_head(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Review"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        feedback = LinearCommentFeedback(
            key="linear-comment:c1:2026-06-07T09:00:00Z",
            id="c1",
            author="reviewer",
            body="Please verify this is already covered.",
            url="https://linear.app/acme/issue/ENG-1#comment-c1",
            created_at="2026-06-07T09:00:00Z",
            updated_at="2026-06-07T09:00:00Z",
        )

        class FakeLinear:
            def __init__(self) -> None:
                self.comments: list[str] = []

            async def issue_comments(self, _issue: object) -> list[LinearCommentFeedback]:
                return [feedback]

            async def issue_context(self, _issue: object) -> str:
                return "# ENG-1: Ship it"

            async def comment(self, _issue_id: str, body: str) -> None:
                self.comments.append(body)

        class FakeGitHub:
            async def create_or_update_pr(self, *_args: object, **_kwargs: object) -> PullRequest:
                raise AssertionError("PR should not be updated for an unchanged feedback pass")

        class TestOrchestrator(Orchestrator):
            def linear_feedback_branch_available(
                self,
                _workspace: WorkspaceConfig,
                _branch: str,
            ) -> bool:
                return True

            def dirty_workspace_repos(self, _workspace: WorkspaceConfig) -> list[str]:
                return []

            def checkout_existing_branch_from_origin(
                self,
                _workspace: WorkspaceConfig,
                _branch: str,
            ) -> None:
                return None

            def repo_heads(self, _workspace: WorkspaceConfig) -> dict[str, str]:
                return {"web": "abc123"}

            def changed_repos_since_heads(
                self,
                _workspace: WorkspaceConfig,
                _before_heads: dict[str, str],
            ) -> dict[str, RepoConfig]:
                return {}

            async def _fix_linear_feedback(self, *_args: object, **_kwargs: object) -> str:
                return "No code changes were needed."

        workspace = WorkspaceConfig(
            path=Path("/tmp/workspace"),
            repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
        )
        linear = FakeLinear()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            status = tmp_path / "status.json"
            settings = Settings(workspace_map={"ENG": workspace}, lock_dir=tmp_path / "locks")
            orchestrator = TestOrchestrator(settings, linear=linear, github=FakeGitHub())
            write_processed_feedback(linear_feedback_state(settings.lock_dir, "ENG-1"), set())
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=status):
                with patch(
                    "linear_codex_orchestrator.orchestrator.has_commits_since_base",
                    return_value=True,
                ):
                    with patch("linear_codex_orchestrator.orchestrator.commit_all") as commit:
                        with patch("linear_codex_orchestrator.orchestrator.push_branch") as push:
                            processed = asyncio.run(orchestrator.process_linear_feedback(issue))
                            payload = read_status()

        self.assertTrue(processed)
        commit.assert_not_called()
        push.assert_not_called()
        self.assertEqual(
            payload["issues"]["ENG-1"]["linear_feedback"],
            "Checked Linear feedback (1 comment)",
        )
        self.assertTrue(linear.comments)
        self.assertIn("No code changes were needed", linear.comments[0])

    def test_linear_feedback_branch_available_accepts_origin_branch(self) -> None:
        workspace = WorkspaceConfig(
            path=Path("/tmp/workspace"),
            repos={
                "web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop"),
                "api": RepoConfig("acme/api", Path("/tmp/workspace/api"), "develop"),
            },
        )
        orchestrator = Orchestrator(Settings(workspace_map={"ENG": workspace}))
        branch = "codex/eng-1-ship-it"

        def fake_branch_exists(path: Path, branch_arg: str) -> bool:
            self.assertEqual(branch_arg, branch)
            return path == workspace.repos["api"].path

        def fake_remote_branch_exists(path: Path, branch_arg: str) -> bool:
            self.assertEqual(branch_arg, branch)
            return path == workspace.repos["web"].path

        with patch("linear_codex_orchestrator.orchestrator.branch_exists", fake_branch_exists):
            with patch("linear_codex_orchestrator.orchestrator.remote_branch_exists", fake_remote_branch_exists):
                self.assertTrue(orchestrator.linear_feedback_branch_available(workspace, branch))

    def test_linear_feedback_branch_prefers_stored_pr_branch_when_issue_title_changed(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Edited title",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Review"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        workspace = WorkspaceConfig(
            path=Path("/tmp/workspace"),
            repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
        )
        original_branch = "codex/eng-1-original-title"
        pr = OpenPullRequest(
            repo="acme/web",
            number=12,
            url="https://github.com/acme/web/pull/12",
            title="ENG-1: Original title",
            head_branch=original_branch,
            base_branch="develop",
        )
        orchestrator = Orchestrator(Settings(workspace_map={"ENG": workspace}))

        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / "status.json"
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=status):
                update_pr_status(
                    pr,
                    "Ready for review",
                    issue="ENG-1",
                    repo_key="web",
                    repo_path=workspace.repos["web"].path,
                )
                with patch.object(orchestrator, "linear_feedback_branch_available", return_value=True):
                    branch = orchestrator.linear_feedback_branch(issue, workspace)

        self.assertEqual(branch, original_branch)

    def test_issue_identifier_prefers_standard_ticket_pattern(self) -> None:
        self.assertEqual(parse_issue_identifier("ENG-123: Ship it"), "ENG-123")
        self.assertEqual(parse_issue_identifier("codex/eng-123-ship-it"), "ENG-123")
        self.assertIsNone(parse_issue_identifier("no ticket 123"))

    def test_orchestrator_enriches_issue_status_progressively(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": "Raw Linear description",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "Todo"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )

        class FakeLinear:
            async def issue_context(self, _issue: object) -> str:
                return "# ENG-1: Ship it\n\nFull Linear context"

            async def comment(self, _issue_id: str, _body: str) -> None:
                return None

            async def remove_label(self, _issue_id: str, _label: str) -> None:
                return None

        class TestOrchestrator(Orchestrator):
            def dirty_workspace_repos(self, _workspace: WorkspaceConfig) -> list[str]:
                return []

            async def _plan(self, _issue: object, _workspace: object, _issue_context: str) -> str:
                return "Planner brief"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = WorkspaceConfig(
                path=tmp_path,
                repos={"web": RepoConfig("acme/web", tmp_path / "web", "main")},
            )
            settings = Settings(workspace_map={"ENG": workspace}, lock_dir=tmp_path / "locks")
            status_file = tmp_path / "status.json"
            orchestrator = TestOrchestrator(settings, linear=FakeLinear(), github=object())

            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=status_file):
                with patch("linear_codex_orchestrator.orchestrator.ensure_branch", side_effect=RuntimeError("stop after plan")):
                    with patch("linear_codex_orchestrator.orchestrator.update_issue_status", wraps=update_issue_status) as updates:
                        with self.assertRaisesRegex(RuntimeError, "stop after plan"):
                            asyncio.run(orchestrator._process_locked_issue(issue, workspace, resume=False))
                        status = read_status()

        update_steps = [
            (call.args[1], call.kwargs)
            for call in updates.call_args_list
            if call.args and call.args[0] == issue
        ]
        self.assertEqual(update_steps[0][0], "Starting")
        self.assertEqual(update_steps[0][1]["branch"], branch_name("ENG-1", "Ship it"))
        self.assertEqual(update_steps[0][1]["description"], "Raw Linear description")
        self.assertEqual(update_steps[0][1]["context_status"], "metadata")
        self.assertEqual(update_steps[1][0], "Linear context loaded")
        self.assertEqual(update_steps[1][1]["branch"], branch_name("ENG-1", "Ship it"))
        self.assertEqual(update_steps[1][1]["issue_context"], "# ENG-1: Ship it\n\nFull Linear context")
        self.assertEqual(update_steps[1][1]["context_status"], "linear_context")
        planned_step = next(step for step in update_steps if step[0] == "Planning complete")
        self.assertEqual(planned_step[1]["branch"], branch_name("ENG-1", "Ship it"))
        self.assertEqual(planned_step[1]["planner_brief"], "Planner brief")
        self.assertEqual(planned_step[1]["context_status"], "planned")
        self.assertEqual(status["issues"]["ENG-1"]["branch"], branch_name("ENG-1", "Ship it"))
        self.assertEqual(status["issues"]["ENG-1"]["description"], "Raw Linear description")
        self.assertEqual(status["issues"]["ENG-1"]["issue_context"], "# ENG-1: Ship it\n\nFull Linear context")
        self.assertEqual(status["issues"]["ENG-1"]["planner_brief"], "Planner brief")
        self.assertEqual(status["issues"]["ENG-1"]["context_status"], "planned")

    def test_archive_stale_prs_moves_only_missing_prs_for_repo(self) -> None:
        class FakeGitHub:
            async def pr_archive_status(self, _repo: str, number: int) -> str:
                return "Merged" if number == 12 else "Closed"

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text(
                json.dumps(
                    {
                        "issues": {
                            "ENG-1": {
                                "identifier": "ENG-1",
                                "status": "PR ready",
                                "prs": "https://github.com/acme/web/pull/12",
                                "updated_at": "2026-01-01T00:00:00",
                            }
                        },
                        "prs": {
                            "acme/web#12": {
                                "key": "acme/web#12",
                                "repo": "acme/web",
                                "number": 12,
                                "url": "https://github.com/acme/web/pull/12",
                                "issue": "ENG-1",
                                "status": "Ready",
                                "updated_at": "2026-01-01T00:00:00",
                            },
                            "acme/web#13": {
                                "key": "acme/web#13",
                                "repo": "acme/web",
                                "number": 13,
                                "status": "Ready",
                                "updated_at": "2026-01-01T00:00:01",
                            },
                        },
                        "archived_prs": {},
                    }
                ),
                encoding="utf-8",
            )
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=path):
                asyncio.run(
                    archive_stale_prs(
                        FakeGitHub(),
                        "acme/web",
                        [
                            OpenPullRequest(
                                repo="acme/web",
                                number=13,
                                url="https://github.com/acme/web/pull/13",
                                title="Open",
                                head_branch="codex/open",
                                base_branch="main",
                            )
                        ],
                    )
                )
                status = read_status()

        self.assertIn("acme/web#13", status["prs"])
        self.assertTrue(status["prs"]["acme/web#12"]["archived"])
        self.assertEqual(status["archived_prs"]["acme/web#12"]["status"], "Merged")
        self.assertIn("archived_at", status["archived_prs"]["acme/web#12"])
        self.assertEqual(status["issues"]["ENG-1"]["status"], "Done")
        self.assertEqual(status["issues"]["ENG-1"]["merged_prs"], "https://github.com/acme/web/pull/12")

    def test_archive_stale_prs_preserves_other_repos(self) -> None:
        class FakeGitHub:
            async def pr_archive_status(self, _repo: str, _number: int) -> str:
                return "Merged"

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text(
                json.dumps(
                    {
                        "prs": {
                            "acme/web#12": {"key": "acme/web#12", "repo": "acme/web", "number": 12},
                            "acme/api#7": {"key": "acme/api#7", "repo": "acme/api", "number": 7},
                        },
                        "archived_prs": {
                            "acme/old#1": {
                                "key": "acme/old#1",
                                "repo": "acme/old",
                                "number": 1,
                                "status": "Merged",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=path):
                asyncio.run(archive_stale_prs(FakeGitHub(), "acme/web", []))
                status = read_status()

        self.assertTrue(status["prs"]["acme/web#12"]["archived"])
        self.assertIn("acme/api#7", status["prs"])
        self.assertIn("acme/old#1", status["archived_prs"])

    def test_archive_stale_prs_maps_merged_closed_and_lookup_failure(self) -> None:
        class FakeGitHub:
            async def pr_archive_status(self, _repo: str, number: int) -> str:
                if number == 1:
                    return "Merged"
                if number == 2:
                    return "Closed"
                raise RuntimeError("lookup failed")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text(
                json.dumps(
                    {
                        "prs": {
                            "acme/web#1": {"key": "acme/web#1", "repo": "acme/web", "number": 1},
                            "acme/web#2": {"key": "acme/web#2", "repo": "acme/web", "number": 2},
                            "acme/web#3": {"key": "acme/web#3", "repo": "acme/web", "number": 3},
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=path):
                asyncio.run(archive_stale_prs(FakeGitHub(), "acme/web", []))
                status = read_status()

        self.assertEqual(status["archived_prs"]["acme/web#1"]["status"], "Merged")
        self.assertEqual(status["archived_prs"]["acme/web#2"]["status"], "Closed")
        self.assertEqual(status["archived_prs"]["acme/web#3"]["status"], "Archived")

    def test_archive_pr_status_marks_pr_archived(self) -> None:
        pr = OpenPullRequest("acme/web", 12, "https://github.com/acme/web/pull/12", "Fix", "branch", "main")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=path):
                update_pr_status(pr, "Ready")
                self.assertTrue(archive_pr_status(pr))
                self.assertFalse(archive_pr_status(pr))
                status = read_status()
        self.assertTrue(status["prs"]["acme/web#12"]["archived"])
        self.assertEqual(status["prs"]["acme/web#12"]["status"], "Merged")
        self.assertIn("archived_at", status["prs"]["acme/web#12"])

    def test_archive_pr_status_marks_issue_done_when_all_associated_prs_are_merged(self) -> None:
        pr = OpenPullRequest(
            "acme/web",
            12,
            "https://github.com/acme/web/pull/12",
            "ENG-1: Ship it",
            "codex/eng-1",
            "main",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text(
                json.dumps(
                    {
                        "issues": {
                            "ENG-1": {
                                "identifier": "ENG-1",
                                "status": "PR ready",
                                "prs": "https://github.com/acme/web/pull/12",
                            }
                        },
                        "prs": {
                            "acme/web#12": {
                                "key": "acme/web#12",
                                "repo": "acme/web",
                                "number": 12,
                                "url": "https://github.com/acme/web/pull/12",
                                "issue": "ENG-1",
                                "status": "Ready for review",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=path):
                self.assertTrue(archive_pr_status(pr))
                status = read_status()

        self.assertEqual(status["issues"]["ENG-1"]["status"], "Done")
        self.assertEqual(status["issues"]["ENG-1"]["merged_prs"], "https://github.com/acme/web/pull/12")
        self.assertIn("pr_merged_at", status["issues"]["ENG-1"])

    def test_archive_pr_status_waits_for_all_associated_prs_before_marking_issue_done(self) -> None:
        pr = OpenPullRequest(
            "acme/web",
            12,
            "https://github.com/acme/web/pull/12",
            "ENG-1: Web",
            "codex/eng-1",
            "main",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text(
                json.dumps(
                    {
                        "issues": {"ENG-1": {"identifier": "ENG-1", "status": "PR ready"}},
                        "prs": {
                            "acme/web#12": {
                                "key": "acme/web#12",
                                "repo": "acme/web",
                                "number": 12,
                                "url": "https://github.com/acme/web/pull/12",
                                "issue": "ENG-1",
                                "status": "Ready for review",
                            },
                            "acme/api#7": {
                                "key": "acme/api#7",
                                "repo": "acme/api",
                                "number": 7,
                                "url": "https://github.com/acme/api/pull/7",
                                "issue": "ENG-1",
                                "status": "Ready for review",
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=path):
                self.assertTrue(archive_pr_status(pr))
                status = read_status()

        self.assertEqual(status["issues"]["ENG-1"]["status"], "PR ready")
        self.assertNotIn("merged_prs", status["issues"]["ENG-1"])

    def test_web_log_index_and_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch("linear_codex_orchestrator.web_server.LOG_DIR", tmp_path):
                (tmp_path / "orchestrator.log").write_text("[time] started\n", encoding="utf-8")
                raw = (
                    "diff --git a/src/app.py b/src/app.py\n"
                    "+added\n"
                    "-removed\n"
                    "tokens used\n"
                    "12.500\n"
                    "Implemented the change.\n\nValidation passed."
                )
                (tmp_path / "stage.log").write_text(raw, encoding="utf-8")
                names = [entry["name"] for entry in log_index()]
                self.assertIn("orchestrator.log", names)
                self.assertIn("stage.log", names)
                stage = next(entry for entry in log_index() if entry["name"] == "stage.log")
                self.assertEqual(stage["summary"]["headline"], "Implemented the change.")
                self.assertEqual(stage["summary"]["tokens_used"], 12500.0)
                self.assertEqual(stage["summary"]["files"][0]["path"], "src/app.py")
                html = render_missing_frontend()
                self.assertIn("Linear Codex Orchestrator", html)
                self.assertIn("React dashboard has not been built", html)
                self.assertNotIn("http-equiv=\"refresh\"", html)

    def test_codex_log_summary_extracts_final_message_tokens_and_files(self) -> None:
        raw = (
            "OpenAI Codex\n"
            "diff --git a/src/app.py b/src/app.py\n"
            "+added\n"
            "-removed\n"
            "tokens used\n"
            "42.250\n"
            "Removed the PR feedback limit.\n\nValidation passed."
        )
        summary = summarize_codex_log(Path(".logs/stage.log"), raw, "")
        self.assertEqual(summary["headline"], "Removed the PR feedback limit.")
        self.assertEqual(summary["tokens_used"], 42250.0)
        self.assertEqual(summary["files"], [{"path": "src/app.py", "added": 1, "removed": 1}])
        self.assertEqual(summary["summary_version"], 5)
        self.assertEqual(summary["last_line"], "Validation passed.")

    def test_codex_log_summary_marks_partial_logs_as_running(self) -> None:
        summary = summarize_codex_log(Path(".logs/stage.log"), "OpenAI Codex\nworking\n", "")
        self.assertEqual(summary["status"], "running")
        self.assertEqual(summary["headline"], "Running. Waiting for Codex final message.")
        self.assertEqual(summary["message"], "")
        self.assertEqual(summary["last_line"], "working")

    def test_codex_log_summary_extracts_last_interesting_line(self) -> None:
        self.assertEqual(last_interesting_line("\nexec\n\x1b[32mRunning tests\x1b[0m\n"), "Running tests")

    def test_codex_log_summary_parses_dot_separated_token_thousands(self) -> None:
        self.assertEqual(tokens_used("\ntokens used\n135.878\nDone."), 135878.0)
        self.assertEqual(tokens_used("\ntokens used\n1.234.567\nDone."), 1234567.0)

    def test_codex_log_summary_deduplicates_repo_prefixed_file_paths(self) -> None:
        raw = (
            "diff --git a/product-web/src/app.ts b/product-web/src/app.ts\n"
            "+repo added\n"
            "-repo removed\n"
            "diff --git a/src/app.ts b/src/app.ts\n"
            "+plain added\n"
            "See [file](/tmp/workspace/product-web/src/app.ts:1)\n"
            "tokens used\n1\nDone."
        )
        summary = summarize_codex_log(Path(".logs/stage.log"), raw, "")
        self.assertEqual(summary["files"], [{"path": "src/app.ts", "added": 2, "removed": 1}])

    def test_codex_log_summary_is_written_next_to_raw_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "stage.log"
            raw = "tokens used\n1\nDone."
            log_path.write_text(raw, encoding="utf-8")
            write_log_summary(log_path, raw, "Done.")
            summary_path = Path(tmp) / "stage.summary.json"
            self.assertTrue(summary_path.is_file())
            self.assertIn("Done.", summary_path.read_text(encoding="utf-8"))

    def test_hidden_codex_process_streams_to_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "stage.log"
            output = _run_process_hidden(
                ["python3", "-c", "import sys, time; print('started'); sys.stdout.flush(); time.sleep(0.1); print('done')"],
                Path(tmp),
                5,
                log_path,
            )
            self.assertEqual(output.returncode, 0)
            self.assertEqual(log_path.read_text(encoding="utf-8"), "started\ndone\n")

    def test_web_task_index_groups_stage_logs_by_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch("linear_codex_orchestrator.web_server.LOG_DIR", tmp_path):
                (tmp_path / "20260517-090000-eng-75-planner.log").write_text("tokens used\n1\nPlanned.", encoding="utf-8")
                (tmp_path / "20260517-091000-eng-75-review.log").write_text("tokens used\n2\nReviewed.", encoding="utf-8")
                (tmp_path / "20260517-092000-data-48-pr-feedback.log").write_text("tokens used\n3\nFixed PR.", encoding="utf-8")
                tasks = task_index()
        self.assertEqual(tasks[0]["key"], "data-48")
        self.assertEqual(tasks[0]["type"], "PR feedback")
        self.assertEqual(tasks[1]["key"], "eng-75")
        self.assertEqual(tasks[1]["log_count"], 2)
        self.assertEqual(len(tasks[1]["stages"]), 2)

    def test_web_task_index_keeps_latest_stage_headline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            older = tmp_path / "20260517-090000-eng-75-planner.log"
            newer = tmp_path / "20260517-091000-eng-75-review.log"
            with patch("linear_codex_orchestrator.web_server.LOG_DIR", tmp_path):
                older.write_text("tokens used\n1\nOlder planner headline.", encoding="utf-8")
                newer.write_text("tokens used\n2\nLatest review headline.", encoding="utf-8")
                os.utime(older, (1_779_000_000, 1_779_000_000))
                os.utime(newer, (1_779_003_600, 1_779_003_600))

                tasks = task_index()

        self.assertEqual(tasks[0]["headline"], "Latest review headline.")

    def test_task_from_log_name_extracts_linear_and_pr_feedback_tasks(self) -> None:
        self.assertEqual(task_from_log_name("20260517-090000-eng-75-review.log")["key"], "eng-75")
        self.assertEqual(task_from_log_name("20260517-092000-data-48-pr-feedback.log")["type"], "PR feedback")

    def test_web_log_paths_are_confined_to_log_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch("linear_codex_orchestrator.web_server.LOG_DIR", tmp_path):
                self.assertEqual(safe_log_path("stage.log"), (tmp_path / "stage.log").resolve())
                with self.assertRaises(ValueError):
                    safe_log_path("../secret")

    def test_web_frontend_paths_are_confined_to_dist_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch("linear_codex_orchestrator.web_server.FRONTEND_DIST", tmp_path):
                self.assertEqual(safe_frontend_path("/assets/app.js"), (tmp_path / "assets/app.js").resolve())
                with self.assertRaises(ValueError):
                    safe_frontend_path("/../secret")

    def test_web_status_index_reads_summary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch("linear_codex_orchestrator.web_server.LOG_DIR", tmp_path):
                (tmp_path / "status.json").write_text(
                    '{"issues":{"ENG-1":{"identifier":"ENG-1","status":"Done","project":"Project X",'
                    '"workspace_path":"/tmp/workspace","repos":[{"key":"web","path":"/tmp/workspace/web"}],'
                    '"prs":"https://github.com/acme/web/pull/1",'
                    '"updated_at":"2026-01-01T00:00:00"}},'
                    '"prs":{'
                    '"acme/web#1":{"key":"acme/web#1","status":"Ready","repo_path":"/tmp/workspace/web",'
                    '"updated_at":"2026-01-01T00:00:01"},'
                    '"acme/web#2":{"key":"acme/web#2","status":"Ready","issue":"ENG-1",'
                    '"repo_path":"/tmp/workspace/web","updated_at":"2026-01-01T00:00:02"}},'
                    '"archived_prs":{'
                    '"acme/web#3":{"key":"acme/web#3","status":"Merged",'
                    '"archived_at":"2026-01-01T00:00:03"},'
                    '"acme/web#4":{"key":"acme/web#4","status":"Merged","issue":"ENG-1",'
                    '"archived_at":"2026-01-01T00:00:04"}}}',
                    encoding="utf-8",
                )
                summary = status_index()
        self.assertEqual(summary["issues"][0]["identifier"], "ENG-1")
        self.assertEqual(summary["issues"][0]["project"], "Project X")
        self.assertEqual(summary["issues"][0]["prs"], "https://github.com/acme/web/pull/1")
        self.assertEqual(summary["prs"][0]["key"], "acme/web#1")
        self.assertEqual(summary["prs"][0]["repo_path"], "/tmp/workspace/web")
        self.assertEqual([item["key"] for item in summary["prs"]], ["acme/web#1"])
        self.assertEqual([item["key"] for item in summary["archived_prs"]], ["acme/web#3"])

    def test_archive_status_item_moves_status_entries_to_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch("linear_codex_orchestrator.web_server.LOG_DIR", tmp_path):
                (tmp_path / "status.json").write_text(
                    '{"issues":{"ENG-1":{"identifier":"ENG-1"}},'
                    '"prs":{"acme/web#1":{"key":"acme/web#1"}}}',
                    encoding="utf-8",
                )
                self.assertTrue(archive_status_item("issue", "ENG-1"))
                self.assertTrue(archive_status_item("pr", "acme/web#1"))
                self.assertFalse(archive_status_item("issue", "ENG-2"))
                summary = status_index()
        self.assertEqual(summary["issues"], [])
        self.assertEqual(summary["prs"], [])
        self.assertEqual(summary["archived_issues"][0]["identifier"], "ENG-1")
        self.assertEqual(summary["archived_prs"][0]["key"], "acme/web#1")

    def test_update_status_item_changes_status_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch("linear_codex_orchestrator.web_server.LOG_DIR", tmp_path):
                (tmp_path / "status.json").write_text(
                    '{"issues":{"ENG-1":{"identifier":"ENG-1","status":"Ready"}},'
                    '"prs":{"acme/web#1":{"key":"acme/web#1","status":"Open"}}}',
                    encoding="utf-8",
                )
                self.assertTrue(update_status_item("issue", "ENG-1", "Done"))
                self.assertTrue(update_status_item("pr", "acme/web#1", "Merged"))
                self.assertFalse(update_status_item("issue", "ENG-2", "Done"))
                summary = status_index()
        self.assertEqual(summary["issues"][0]["status"], "Done")
        self.assertEqual(summary["prs"][0]["status"], "Merged")

    def test_web_config_index_reads_sqlite_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            workspace = tmp_path / "workspace"
            repo = workspace / "web"
            repo.mkdir(parents=True)
            (repo / ".git").mkdir()
            config_path = tmp_path / "config.db"
            write_config_file(
                {
                    "workspace_map": {
                        "ENG": {
                            "path": str(workspace),
                            "repos": {"web": {"github": "acme/web", "path": str(repo)}},
                        }
                    }
                },
                config_path,
            )
            with patch.dict(os.environ, {"ORCHESTRATOR_CONFIG_PATH": str(config_path)}):
                with patch("linear_codex_orchestrator.config.validate_workspace_map"):
                    summary = config_index()
        self.assertTrue(summary["exists"])
        self.assertEqual(summary["config"]["workspace_map"]["ENG"]["repos"]["web"]["github"], "acme/web")

    def test_linear_teams_endpoint_prefers_request_api_key(self) -> None:
        seen_keys: list[str] = []

        class FakeLinearApiClient:
            def __init__(self, api_key: str) -> None:
                seen_keys.append(api_key)

            async def teams(self) -> list[LinearTeam]:
                return [LinearTeam(id="team-id", key="ENG", name="Engineering")]

        with patch("linear_codex_orchestrator.web_server.config_payload_from_env", return_value={"linear_api_key": "lin_env"}):
            with patch("linear_codex_orchestrator.web_server.read_config_file", return_value={"linear_api_key": "lin_saved"}):
                with patch("linear_codex_orchestrator.web_server.LinearApiClient", FakeLinearApiClient):
                    summary = linear_teams_index({"linear_api_key": "lin_request"})

        self.assertEqual(seen_keys, ["lin_request"])
        self.assertEqual(summary, {"ok": True, "source": "api", "teams": [{"id": "team-id", "key": "ENG", "name": "Engineering"}]})

    def test_linear_teams_endpoint_uses_saved_or_env_api_key_before_mcp(self) -> None:
        seen_keys: list[str] = []

        class FakeLinearApiClient:
            def __init__(self, api_key: str) -> None:
                seen_keys.append(api_key)

            async def teams(self) -> list[LinearTeam]:
                return []

        with patch("linear_codex_orchestrator.web_server.config_payload_from_env", return_value={"linear_api_key": "lin_env"}):
            with patch("linear_codex_orchestrator.web_server.read_config_file", return_value={"codex_model": "gpt-5.5"}):
                with patch("linear_codex_orchestrator.web_server.LinearApiClient", FakeLinearApiClient):
                    summary = linear_teams_index({})

        self.assertEqual(seen_keys, ["lin_env"])
        self.assertEqual(summary["source"], "api")

    def test_linear_teams_endpoint_falls_back_to_mcp_with_codex_settings(self) -> None:
        seen: list[dict[str, object]] = []

        class FakeLocalLinearClient:
            def __init__(self, cwd: Path, **kwargs: object) -> None:
                seen.append({"cwd": cwd, **kwargs})

            async def teams(self, timeout_seconds: int) -> list[LinearTeam]:
                seen.append({"timeout_seconds": timeout_seconds})
                return [LinearTeam(id="team-id", key="CODEX", name="Codex Orchestrator")]

        with patch("linear_codex_orchestrator.web_server.config_payload_from_env", return_value={}):
            with patch("linear_codex_orchestrator.web_server.read_config_file", return_value={"codex_model": "saved-model"}):
                with patch("linear_codex_orchestrator.web_server.LocalLinearClient", FakeLocalLinearClient):
                    summary = linear_teams_index({
                        "codex_model": "request-model",
                        "codex_reasoning_effort": "low",
                        "codex_fast_mode": True,
                    })

        self.assertEqual(seen[0]["model"], "request-model")
        self.assertEqual(seen[0]["reasoning_effort"], "low")
        self.assertEqual(seen[0]["fast_mode"], True)
        self.assertEqual(seen[1]["timeout_seconds"], 20)
        self.assertEqual(summary["source"], "mcp")
        self.assertEqual(summary["teams"], [{"id": "team-id", "key": "CODEX", "name": "Codex Orchestrator"}])

    def test_linear_teams_endpoint_fails_softly(self) -> None:
        class FakeLinearApiClient:
            def __init__(self, _api_key: str) -> None:
                return None

            async def teams(self) -> list[LinearTeam]:
                raise RuntimeError("Linear API GraphQL error")

        with patch("linear_codex_orchestrator.web_server.config_payload_from_env", return_value={}):
            with patch("linear_codex_orchestrator.web_server.read_config_file", return_value={"linear_api_key": "lin_saved"}):
                with patch("linear_codex_orchestrator.web_server.LinearApiClient", FakeLinearApiClient):
                    summary = linear_teams_index({})

        self.assertEqual(summary["ok"], False)
        self.assertEqual(summary["source"], "api")
        self.assertEqual(summary["teams"], [])
        self.assertIn("Linear API GraphQL error", str(summary["error"]))

    def test_web_browse_index_lists_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "repo").mkdir()
            (tmp_path / "file.txt").write_text("ignored", encoding="utf-8")
            summary = browse_index(str(tmp_path))
        self.assertEqual(summary["path"], str(tmp_path.resolve()))
        self.assertEqual(summary["directories"], [{"name": "repo", "path": str((tmp_path / "repo").resolve())}])

    def test_web_browse_index_detects_child_git_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_path = tmp_path / "product-api"
            repo_path.mkdir()
            run_git(repo_path, "init")
            run_git(repo_path, "checkout", "-b", "develop")
            run_git(repo_path, "remote", "add", "origin", "git@github.com:acme/product-api.git")
            summary = browse_index(str(tmp_path))
        self.assertEqual(
            summary["repositories"],
            [{
                "key": "product-api",
                "path": str(repo_path.resolve()),
                "github": "acme/product-api",
                "base": "develop",
            }],
        )

    def test_web_browse_index_detects_current_git_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_git(tmp_path, "init")
            run_git(tmp_path, "checkout", "-b", "develop")
            run_git(tmp_path, "remote", "add", "origin", "git@github.com:acme/product-api.git")
            summary = browse_index(str(tmp_path))
        self.assertEqual(
            summary["current_repository"],
            {
                "key": tmp_path.name,
                "path": str(tmp_path.resolve()),
                "github": "acme/product-api",
                "base": "develop",
            },
        )
        self.assertEqual(summary["repositories"], [])

    def test_web_browse_index_returns_current_and_child_git_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            child_path = tmp_path / "product-api"
            child_path.mkdir()
            run_git(tmp_path, "init")
            run_git(child_path, "init")
            run_git(child_path, "checkout", "-b", "develop")
            summary = browse_index(str(tmp_path))
        self.assertEqual(summary["current_repository"]["path"], str(tmp_path.resolve()))
        self.assertEqual(
            summary["repositories"],
            [{
                "key": "product-api",
                "path": str(child_path.resolve()),
                "github": None,
                "base": "develop",
            }],
        )

    def test_github_repo_index_parses_accessible_repos(self) -> None:
        completed = type("Completed", (), {
            "stdout": (
                '{"nameWithOwner":"acme/api","permission":"WRITE"}\n'
                '{"nameWithOwner":"acme/web","permission":"READ"}\n'
            )
        })()
        with patch("linear_codex_orchestrator.web_server.subprocess.run", return_value=completed):
            summary = github_repo_index()
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["repos"][0]["nameWithOwner"], "acme/api")
        self.assertEqual(summary["repos"][1]["permission"], "READ")

    def test_tail_text_limits_large_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.log"
            path.write_text("abcdef", encoding="utf-8")
            self.assertEqual(tail_text(path, 3), "def")

    def test_start_log_server_returns_none_when_port_is_busy(self) -> None:
        server = start_log_server("127.0.0.1", 0)
        self.assertIsNotNone(server)
        assert server is not None
        host, port = server.server_address
        try:
            self.assertIsNone(start_log_server(host, port))
        finally:
            server.shutdown()
            server.server_close()

    def test_run_codex_skips_git_repo_check_for_workspace_roots(self) -> None:
        command = build_codex_command(
            "hello",
            Path("/tmp/workspace"),
            sandbox="workspace-write",
            model=None,
            reasoning_effort=None,
            fast_mode=False,
            output_file_path=Path("/tmp/output.txt"),
        )
        self.assertIn("--skip-git-repo-check", command)

    def test_run_codex_can_bypass_approvals_for_noninteractive_mutations(self) -> None:
        command = build_codex_command(
            "hello",
            Path("/tmp/workspace"),
            sandbox="workspace-write",
            model=None,
            reasoning_effort=None,
            fast_mode=False,
            output_file_path=Path("/tmp/output.txt"),
            bypass_approvals=True,
        )
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", command)

    def test_run_codex_can_set_reasoning_and_fast_mode(self) -> None:
        command = build_codex_command(
            "hello",
            Path("/tmp/workspace"),
            sandbox="workspace-write",
            model=None,
            reasoning_effort="low",
            fast_mode=True,
            output_file_path=Path("/tmp/output.txt"),
        )
        self.assertIn('model_reasoning_effort="low"', command)
        self.assertIn('model_service_tier="priority"', command)

    def test_parse_json_object_uses_last_complete_object(self) -> None:
        raw = 'codex\n{"issues":[]}\nmcp: linear/list_issues completed\ncodex\n{"issues":[{"id":"ENG-79"}]}'
        self.assertEqual(parse_json_object(raw), {"issues": [{"id": "ENG-79"}]})

    def test_remove_label_treats_absent_label_as_success(self) -> None:
        calls: list[str] = []

        def fake_run_codex(prompt: str, *_args: object, **_kwargs: object) -> str:
            calls.append(prompt)
            return '{"success":true,"message":"removed"}'

        with patch("linear_codex_orchestrator.local_linear_client.run_codex", fake_run_codex):
            asyncio.run(LocalLinearClient(Path("/tmp/workspace")).remove_label("ENG-79", "agent-running"))

        self.assertIn('Remove Linear label exactly "agent-running"', calls[0])
        self.assertIn("If the label is already absent, treat the mutation as successful.", calls[0])

    def test_linear_mutation_retries_transient_failures(self) -> None:
        calls: list[str] = []

        def fake_run_codex(prompt: str, *_args: object, **_kwargs: object) -> str:
            calls.append(prompt)
            if len(calls) == 1:
                return '{"success":false,"message":"Auth required"}'
            return '{"success":true,"message":"posted"}'

        async def fake_sleep(_delay: float) -> None:
            return None

        client = LocalLinearClient(Path("/tmp/workspace"))
        client.mutation_retry_delays = (0.0,)
        with patch("linear_codex_orchestrator.local_linear_client.run_codex", fake_run_codex):
            with patch("linear_codex_orchestrator.local_linear_client.asyncio.sleep", fake_sleep):
                asyncio.run(client.comment("ENG-79", "done"))

        self.assertEqual(len(calls), 2)

    def test_linear_mutation_does_not_retry_semantic_failures(self) -> None:
        calls: list[str] = []

        def fake_run_codex(prompt: str, *_args: object, **_kwargs: object) -> str:
            calls.append(prompt)
            return '{"success":false,"message":"status does not exist"}'

        client = LocalLinearClient(Path("/tmp/workspace"))
        client.mutation_retry_delays = (0.0,)
        with patch("linear_codex_orchestrator.local_linear_client.run_codex", fake_run_codex):
            with self.assertRaisesRegex(RuntimeError, "status does not exist"):
                asyncio.run(client.move_issue("ENG-79", "Missing"))

        self.assertEqual(len(calls), 1)

    def test_transient_linear_error_classification(self) -> None:
        self.assertTrue(is_transient_linear_error(RuntimeError("Linear MCP Auth required")))
        self.assertTrue(is_transient_linear_error(RuntimeError("request timed out")))
        self.assertFalse(is_transient_linear_error(RuntimeError("status does not exist")))

    def test_ready_issues_prompt_can_scope_team_keys(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        def fake_run_codex(prompt: str, *_args: object, **kwargs: object) -> str:
            calls.append((prompt, kwargs))
            return '{"issues":[]}'

        with patch("linear_codex_orchestrator.local_linear_client.run_codex", fake_run_codex):
            asyncio.run(LocalLinearClient(Path("/tmp/workspace")).ready_issues("Todo", None, 1, team_keys=("ENG",)))

        self.assertIn('team key in "ENG"', calls[0][0])
        self.assertIn("project_name, project_url", calls[0][0])
        self.assertIn("Do not read local files", calls[0][0])
        self.assertFalse(calls[0][1]["show_output"])

    def test_linear_mcp_team_lookup_uses_schema_read_only_and_short_timeout(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        def fake_run_codex(prompt: str, *_args: object, **kwargs: object) -> str:
            calls.append((prompt, kwargs))
            return '{"teams":[{"id":"team-id","key":"eng","name":"Engineering"}]}'

        with patch("linear_codex_orchestrator.local_linear_client.run_codex", fake_run_codex):
            teams = asyncio.run(LocalLinearClient(Path("/tmp/workspace")).teams(timeout_seconds=7))

        self.assertEqual(teams, [LinearTeam(id="team-id", key="ENG", name="Engineering")])
        self.assertIn("List the visible Linear teams only.", calls[0][0])
        self.assertIn("Do not read local files", calls[0][0])
        self.assertEqual(calls[0][1]["sandbox"], "read-only")
        self.assertEqual(calls[0][1]["timeout_seconds"], 7)
        self.assertEqual(calls[0][1]["output_schema"]["required"], ["teams"])
        self.assertFalse(calls[0][1]["show_output"])

    def test_issue_context_runs_quietly_and_uses_only_linear_mcp(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Context",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Progress"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        calls: list[tuple[str, dict[str, object]]] = []

        def fake_run_codex(prompt: str, *_args: object, **kwargs: object) -> str:
            calls.append((prompt, kwargs))
            return "context"

        with patch("linear_codex_orchestrator.local_linear_client.run_codex", fake_run_codex):
            self.assertEqual(asyncio.run(LocalLinearClient(Path("/tmp/workspace")).issue_context(issue)), "context")

        self.assertIn("Do not read local files", calls[0][0])
        self.assertFalse(calls[0][1]["show_output"])

    def test_issue_comments_runs_quietly_and_returns_chronological_comments(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Context",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Progress"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        calls: list[tuple[str, dict[str, object]]] = []

        def fake_run_codex(prompt: str, *_args: object, **kwargs: object) -> str:
            calls.append((prompt, kwargs))
            return json.dumps(
                {
                    "comments": [
                        {
                            "id": "late",
                            "author": "Reviewer",
                            "body": "Second",
                            "url": "",
                            "created_at": "2026-06-07T09:00:00Z",
                            "updated_at": "2026-06-07T09:00:00Z",
                        },
                        {
                            "id": "early",
                            "author": "Reviewer",
                            "body": "First",
                            "url": "",
                            "created_at": "2026-06-07T08:00:00Z",
                            "updated_at": "",
                        },
                    ]
                }
            )

        with patch("linear_codex_orchestrator.local_linear_client.run_codex", fake_run_codex):
            comments = asyncio.run(LocalLinearClient(Path("/tmp/workspace")).issue_comments(issue))

        self.assertEqual([comment.id for comment in comments], ["early", "late"])
        self.assertEqual(comments[0].key, "linear-comment:early:2026-06-07T08:00:00Z")
        self.assertIn("Do not mutate Linear", calls[0][0])
        self.assertEqual(calls[0][1]["output_schema"]["required"], ["comments"])
        self.assertFalse(calls[0][1]["show_output"])

    def test_process_issue_skips_unmapped_team(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "AND-1",
                "title": "Skip me",
                "description": "",
                "url": "https://linear.app/acme/issue/AND-1",
                "state": {"name": "Todo"},
                "team": {"key": "AND", "name": "Other"},
                "labels": {"nodes": []},
            }
        )
        orchestrator = Orchestrator(Settings(workspace_map={}))
        asyncio.run(orchestrator.process_issue(issue))

    def test_reload_settings_updates_runtime_clients(self) -> None:
        class FakeClient:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                self.closed = False

            async def close(self) -> None:
                self.closed = True

        old_linear = FakeClient()
        old_github = FakeClient()
        old_settings = Settings(workspace_map={}, dry_run=False)
        new_settings = Settings(workspace_map={}, dry_run=True)
        orchestrator = Orchestrator(old_settings, linear=old_linear, github=old_github)
        with patch("linear_codex_orchestrator.orchestrator.Settings.from_env", return_value=new_settings):
            with patch("linear_codex_orchestrator.orchestrator.LocalGitHubClient", FakeClient):
                with patch("linear_codex_orchestrator.orchestrator.LocalLinearClient", FakeClient):
                    asyncio.run(orchestrator.reload_settings())

        self.assertEqual(orchestrator.settings, new_settings)
        self.assertTrue(old_linear.closed)
        self.assertTrue(old_github.closed)
        self.assertIsInstance(orchestrator.github, FakeClient)

    def test_run_once_resumes_running_issues_before_ready_issues(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Resume me",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Progress"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": [{"name": "agent-running"}]},
            }
        )

        class FakeLinear:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str | None]] = []

            async def close(self) -> None:
                return None

            async def ready_issues(self, status: str, label: str | None, *_args: object) -> list[object]:
                self.calls.append((status, label))
                return [issue] if label == "agent-running" else []

        class FakeGitHub:
            async def close(self) -> None:
                return None

            async def list_open_prs(self, *_args: object, **_kwargs: object) -> list[object]:
                return []

        linear = FakeLinear()
        settings = Settings(
            workspace_map={
                "ENG": WorkspaceConfig(
                    path=Path("/tmp/workspace"),
                    repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
                )
            }
        )
        orchestrator = Orchestrator(settings, linear=linear, github=FakeGitHub())
        seen: list[tuple[str, bool]] = []

        async def fake_process(issue_arg: object, resume: bool = False) -> None:
            seen.append((issue_arg.identifier, resume))

        orchestrator.process_issue = fake_process  # type: ignore[method-assign]
        asyncio.run(orchestrator.run_once())

        self.assertEqual(
            linear.calls,
            [
                ("In Review", None),
                ("In Progress", "agent-running"),
            ],
        )
        self.assertEqual(seen, [("ENG-1", True)])

    def test_run_pr_feedback_once_archives_merged_pr_statuses(self) -> None:
        merged_pr = OpenPullRequest(
            "acme/web",
            12,
            "https://github.com/acme/web/pull/12",
            "Merged",
            "codex/eng-1",
            "develop",
        )

        class FakeGitHub:
            async def close(self) -> None:
                return None

            async def list_open_prs(self, *_args: object, **_kwargs: object) -> list[object]:
                return []

            async def list_merged_prs(self, *_args: object, **_kwargs: object) -> list[OpenPullRequest]:
                return [merged_pr]

        workspace = WorkspaceConfig(
            path=Path("/tmp/workspace"),
            repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
        )
        orchestrator = Orchestrator(
            Settings(workspace_map={"ENG": workspace}),
            github=FakeGitHub(),
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            with patch("linear_codex_orchestrator.orchestrator.status_path", return_value=path):
                with patch("linear_codex_orchestrator.web_server.LOG_DIR", Path(tmp)):
                    update_pr_status(merged_pr, "Ready for review")
                    asyncio.run(orchestrator.run_pr_feedback_once())
                    summary = status_index()

        self.assertEqual(summary["prs"], [])
        self.assertEqual(summary["archived_prs"][0]["key"], "acme/web#12")

    def test_run_once_resumes_interrupted_in_progress_issue_with_existing_branch(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Resume me",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Progress"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )

        class FakeLinear:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str | None, tuple[str, ...]]] = []

            async def close(self) -> None:
                return None

            async def ready_issues(
                self,
                status: str,
                label: str | None,
                _limit: int,
                exclude_labels: tuple[str, ...] = (),
                *_args: object,
            ) -> list[object]:
                self.calls.append((status, label, exclude_labels))
                return [issue] if status == "In Progress" and label is None else []

        class FakeGitHub:
            async def close(self) -> None:
                return None

            async def list_open_prs(self, *_args: object, **_kwargs: object) -> list[object]:
                return []

        workspace = WorkspaceConfig(
            path=Path("/tmp/workspace"),
            repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
        )
        orchestrator = Orchestrator(
            Settings(workspace_map={"ENG": workspace}),
            linear=FakeLinear(),
            github=FakeGitHub(),
        )
        seen: list[tuple[str, bool]] = []

        async def fake_process(issue_arg: object, resume: bool = False) -> None:
            seen.append((issue_arg.identifier, resume))

        orchestrator.process_issue = fake_process  # type: ignore[method-assign]
        with patch.object(orchestrator, "branch_exists_in_all_repos", return_value=True):
            asyncio.run(orchestrator.run_once())

        self.assertEqual(
            orchestrator.linear.calls,
            [
                ("In Review", None, ("agent-running", "agent-blocked")),
                ("In Progress", "agent-running", ("agent-blocked",)),
                ("In Progress", None, ("agent-running", "agent-blocked")),
            ],
        )
        self.assertEqual(seen, [("ENG-1", True)])

    def test_run_once_ignores_in_progress_issue_without_existing_branch(self) -> None:
        in_progress = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Human work",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Progress"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        todo = parse_linear_issue(
            {
                "id": "def",
                "identifier": "ENG-2",
                "title": "Start me",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-2",
                "state": {"name": "Todo"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )

        class FakeLinear:
            async def close(self) -> None:
                return None

            async def ready_issues(self, status: str, label: str | None, *_args: object) -> list[object]:
                if status == "In Progress" and label is None:
                    return [in_progress]
                if status == "Todo":
                    return [todo]
                return []

        class FakeGitHub:
            async def close(self) -> None:
                return None

            async def list_open_prs(self, *_args: object, **_kwargs: object) -> list[object]:
                return []

        workspace = WorkspaceConfig(
            path=Path("/tmp/workspace"),
            repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
        )
        orchestrator = Orchestrator(
            Settings(workspace_map={"ENG": workspace}),
            linear=FakeLinear(),
            github=FakeGitHub(),
        )
        seen: list[tuple[str, bool]] = []

        async def fake_process(issue_arg: object, resume: bool = False) -> None:
            seen.append((issue_arg.identifier, resume))

        orchestrator.process_issue = fake_process  # type: ignore[method-assign]
        with patch.object(orchestrator, "branch_exists_in_all_repos", return_value=False):
            asyncio.run(orchestrator.run_once())

        self.assertEqual(seen, [("ENG-2", False)])

    def test_new_issue_skips_dirty_workspace_before_linear_mutations(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Start me",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "Todo"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )

        class FakeLinear:
            def __init__(self) -> None:
                self.calls: list[str] = []

            async def issue_context(self, _issue: object) -> str:
                self.calls.append("issue_context")
                return "context"

        workspace = WorkspaceConfig(
            path=Path("/tmp/workspace"),
            repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
        )
        linear = FakeLinear()
        orchestrator = Orchestrator(Settings(workspace_map={"ENG": workspace}), linear=linear)
        with patch.object(orchestrator, "dirty_workspace_repos", return_value=["web"]):
            with patch("linear_codex_orchestrator.orchestrator.write_issue_run_state"):
                asyncio.run(orchestrator._process_locked_issue(issue, workspace, resume=False))

        self.assertEqual(linear.calls, [])

    def test_new_issue_prepares_branch_before_moving_to_in_progress(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Start me",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "Todo"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        calls: list[str] = []

        class FakeLinear:
            async def issue_context(self, _issue: object) -> str:
                calls.append("issue_context")
                return "context"

            async def comment(self, _issue_id: str, _body: str) -> None:
                calls.append("comment")

            async def move_issue(self, _issue_id: str, _status_name: str) -> None:
                calls.append("move")

            async def add_label(self, _issue_id: str, _label_name: str) -> None:
                calls.append("add_label")

            async def remove_label(self, _issue_id: str, _label_name: str) -> None:
                calls.append("remove_label")

        workspace = WorkspaceConfig(
            path=Path("/tmp/workspace"),
            repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
        )
        orchestrator = Orchestrator(Settings(workspace_map={"ENG": workspace}), linear=FakeLinear())

        async def fake_plan(*_args: object) -> str:
            calls.append("plan")
            return "plan"

        async def fake_implement(*_args: object) -> str:
            calls.append("implement")
            raise RuntimeError("stop after implementation starts")

        def fake_ensure_branch(*_args: object) -> None:
            calls.append("ensure_branch")

        orchestrator._plan = fake_plan  # type: ignore[method-assign]
        orchestrator._implement = fake_implement  # type: ignore[method-assign]

        with patch.object(orchestrator, "dirty_workspace_repos", return_value=[]):
            with patch("linear_codex_orchestrator.orchestrator.write_issue_run_state"):
                with patch("linear_codex_orchestrator.orchestrator.ensure_branch", fake_ensure_branch):
                    with self.assertRaisesRegex(RuntimeError, "stop after implementation starts"):
                        asyncio.run(orchestrator._process_locked_issue(issue, workspace, resume=False))

        self.assertLess(calls.index("ensure_branch"), calls.index("move"))
        self.assertLess(calls.index("move"), calls.index("implement"))

    def test_issue_run_state_round_trips_in_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "config.db"
            with patch("linear_codex_orchestrator.run_state.config_path", return_value=db_path):
                write_issue_run_state(
                    "abc",
                    "ENG-1",
                    Path("/tmp/workspace"),
                    "codex/eng-1-test",
                    "reviewing",
                    plan="plan",
                    implementation_summary="summary",
                )

                state = read_issue_run_state("abc", Path("/tmp/workspace"))
                self.assertIsNotNone(state)
                assert state is not None
                self.assertEqual(state.issue_identifier, "ENG-1")
                self.assertEqual(state.branch, "codex/eng-1-test")
                self.assertEqual(state.stage, "reviewing")
                self.assertEqual(state.plan, "plan")
                self.assertEqual(state.implementation_summary, "summary")

                write_issue_run_state(
                    "abc",
                    "ENG-1",
                    Path("/tmp/workspace"),
                    "codex/eng-1-test",
                    "optimized",
                )
                state = read_issue_run_state("abc", Path("/tmp/workspace"))
                self.assertIsNotNone(state)
                assert state is not None
                self.assertEqual(state.stage, "optimized")
                self.assertEqual(state.plan, "plan")
                self.assertEqual(state.implementation_summary, "summary")

                clear_issue_run_state("abc", Path("/tmp/workspace"))
                self.assertIsNone(read_issue_run_state("abc", Path("/tmp/workspace")))

    def test_commit_phase_changes_commits_only_dirty_changed_repos(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Progress"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        repo = RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")
        orchestrator = Orchestrator(Settings(workspace_map={}))
        commits: list[tuple[Path, str]] = []

        def fake_has_changes(path: Path) -> bool:
            return path == repo.path

        def fake_commit_all(path: Path, message: str) -> None:
            commits.append((path, message))

        with patch("linear_codex_orchestrator.orchestrator.has_changes", fake_has_changes):
            with patch("linear_codex_orchestrator.orchestrator.commit_all", fake_commit_all):
                orchestrator.commit_phase_changes(issue, {"web": repo}, "optimization")

        self.assertEqual(commits, [(repo.path, "ENG-1: optimization")])

    def test_resume_runs_implementation_before_optimization(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Resume me",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Progress"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": [{"name": "agent-running"}]},
            }
        )

        class FakeLinear:
            async def issue_context(self, _issue: object) -> str:
                return "context"

            async def comment(self, _issue_id: str, _body: str) -> None:
                return None

        workspace = WorkspaceConfig(
            path=Path("/tmp/workspace"),
            repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
        )
        orchestrator = Orchestrator(Settings(workspace_map={"ENG": workspace}), linear=FakeLinear())
        calls: list[str] = []

        async def fake_implement(*_args: object) -> str:
            calls.append("implement")
            return "implementation summary"

        async def fake_optimize(*_args: object) -> str:
            calls.append("optimize")
            raise RuntimeError("stop after optimization starts")

        def fake_changed_repos(_workspace: WorkspaceConfig) -> dict[str, RepoConfig]:
            return workspace.repos

        orchestrator._implement = fake_implement  # type: ignore[method-assign]
        orchestrator._optimize = fake_optimize  # type: ignore[method-assign]
        orchestrator.changed_repos = fake_changed_repos  # type: ignore[method-assign]

        with patch.object(orchestrator, "checkout_existing_branch"):
            with patch.object(orchestrator, "commit_phase_changes"):
                with patch("linear_codex_orchestrator.orchestrator.read_issue_run_state", return_value=None):
                    with patch("linear_codex_orchestrator.orchestrator.write_issue_run_state"):
                        with patch("linear_codex_orchestrator.orchestrator.changed_files", return_value=" M file.py"):
                            with self.assertRaisesRegex(RuntimeError, "stop after optimization starts"):
                                asyncio.run(orchestrator._process_locked_issue(issue, workspace, resume=True))

        self.assertEqual(calls, ["implement", "optimize"])

    def test_resume_from_reviewing_stage_skips_implementation_and_optimization(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Resume review",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "In Progress"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": [{"name": "agent-running"}]},
            }
        )

        class FakeLinear:
            async def issue_context(self, _issue: object) -> str:
                return "context"

            async def comment(self, _issue_id: str, _body: str) -> None:
                return None

        workspace = WorkspaceConfig(
            path=Path("/tmp/workspace"),
            repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
        )
        run_state = IssueRunState(
            issue_id="abc",
            issue_identifier="ENG-1",
            workspace_path="/tmp/workspace",
            branch="codex/eng-1-resume-review",
            stage="reviewing",
            plan="saved plan",
            implementation_summary="saved summary",
        )
        orchestrator = Orchestrator(Settings(workspace_map={"ENG": workspace}), linear=FakeLinear())
        calls: list[str] = []

        async def fake_implement(*_args: object) -> str:
            calls.append("implement")
            return "implementation summary"

        async def fake_optimize(*_args: object) -> str:
            calls.append("optimize")
            return "optimization summary"

        async def fake_review(*_args: object) -> object:
            calls.append("review")
            raise RuntimeError("stop at review")

        orchestrator._implement = fake_implement  # type: ignore[method-assign]
        orchestrator._optimize = fake_optimize  # type: ignore[method-assign]
        orchestrator._review = fake_review  # type: ignore[method-assign]
        orchestrator.changed_repos = lambda _workspace: workspace.repos  # type: ignore[method-assign]

        with patch.object(orchestrator, "checkout_existing_branch"):
            with patch("linear_codex_orchestrator.orchestrator.read_issue_run_state", return_value=run_state):
                with patch("linear_codex_orchestrator.orchestrator.write_issue_run_state"):
                    with self.assertRaisesRegex(RuntimeError, "stop at review"):
                        asyncio.run(orchestrator._process_locked_issue(issue, workspace, resume=True))

        self.assertEqual(calls, ["review"])

    def test_resume_plan_continues_partial_implementation(self) -> None:
        plan = resume_plan()

        self.assertIn("Continue the implementation", plan)
        self.assertIn("partial implementation work", plan)

    def test_resume_checks_out_existing_branch_without_resetting_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            branch = "codex/eng-1-resume-me"
            run_commands: list[tuple[str, ...]] = []

            def fake_run_git(path: Path, *args: str) -> str:
                self.assertEqual(path, repo_path)
                run_commands.append(args)
                if args == ("show-ref", "--verify", "--quiet", f"refs/heads/{branch}"):
                    return ""
                if args == ("checkout", branch):
                    return ""
                raise AssertionError(f"unexpected git command: {args}")

            workspace = WorkspaceConfig(
                path=repo_path,
                repos={"web": RepoConfig("acme/web", repo_path, "develop")},
            )
            orchestrator = Orchestrator(Settings(workspace_map={"ENG": workspace}))
            with patch("linear_codex_orchestrator.git_ops.run_git", fake_run_git):
                with patch("linear_codex_orchestrator.orchestrator.run_git", fake_run_git):
                    orchestrator.checkout_existing_branch(workspace, branch)

        self.assertEqual(
            run_commands,
            [
                ("show-ref", "--verify", "--quiet", f"refs/heads/{branch}"),
                ("checkout", branch),
            ],
        )

    def test_linear_feedback_refreshes_existing_branch_from_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            branch = "codex/eng-1-review-me"
            run_commands: list[tuple[str, ...]] = []

            def fake_run_git(path: Path, *args: str) -> str:
                self.assertEqual(path, repo_path)
                run_commands.append(args)
                if args in {
                    ("fetch", "origin", branch),
                    ("checkout", "-B", branch, f"origin/{branch}"),
                }:
                    return ""
                raise AssertionError(f"unexpected git command: {args}")

            workspace = WorkspaceConfig(
                path=repo_path,
                repos={"web": RepoConfig("acme/web", repo_path, "develop")},
            )
            orchestrator = Orchestrator(Settings(workspace_map={"ENG": workspace}))
            with patch("linear_codex_orchestrator.orchestrator.remote_branch_exists", return_value=True):
                with patch("linear_codex_orchestrator.orchestrator.run_git", fake_run_git):
                    orchestrator.checkout_existing_branch_from_origin(workspace, branch)

        self.assertEqual(
            run_commands,
            [
                ("fetch", "origin", branch),
                ("checkout", "-B", branch, f"origin/{branch}"),
            ],
        )

    def test_linear_feedback_keeps_local_branch_when_origin_branch_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            changed_repo = tmp_path / "changed"
            untouched_repo = tmp_path / "untouched"
            branch = "codex/eng-1-review-me"
            run_commands: list[tuple[Path, tuple[str, ...]]] = []

            def fake_remote_branch_exists(path: Path, branch_arg: str) -> bool:
                self.assertEqual(branch_arg, branch)
                return path == changed_repo

            def fake_branch_exists(path: Path, branch_arg: str) -> bool:
                self.assertEqual(branch_arg, branch)
                return path == untouched_repo

            def fake_run_git(path: Path, *args: str) -> str:
                run_commands.append((path, args))
                if path == changed_repo and args in {
                    ("fetch", "origin", branch),
                    ("checkout", "-B", branch, f"origin/{branch}"),
                }:
                    return ""
                if path == untouched_repo and args == ("checkout", branch):
                    return ""
                raise AssertionError(f"unexpected git command: {path} {args}")

            workspace = WorkspaceConfig(
                path=tmp_path,
                repos={
                    "changed": RepoConfig("acme/changed", changed_repo, "develop"),
                    "untouched": RepoConfig("acme/untouched", untouched_repo, "develop"),
                },
            )
            orchestrator = Orchestrator(Settings(workspace_map={"ENG": workspace}))
            with patch("linear_codex_orchestrator.orchestrator.remote_branch_exists", fake_remote_branch_exists):
                with patch("linear_codex_orchestrator.orchestrator.branch_exists", fake_branch_exists):
                    with patch("linear_codex_orchestrator.git_ops.run_git", fake_run_git):
                        with patch("linear_codex_orchestrator.orchestrator.run_git", fake_run_git):
                            orchestrator.checkout_existing_branch_from_origin(workspace, branch)

        self.assertEqual(
            run_commands,
            [
                (changed_repo, ("fetch", "origin", branch)),
                (changed_repo, ("checkout", "-B", branch, f"origin/{branch}")),
                (untouched_repo, ("checkout", branch)),
            ],
        )

    def test_linear_feedback_recreates_missing_branch_from_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            branch = "codex/eng-1-review-me"
            ensured: list[tuple[Path, str, str]] = []

            workspace = WorkspaceConfig(
                path=repo_path,
                repos={"web": RepoConfig("acme/web", repo_path, "develop")},
            )
            orchestrator = Orchestrator(Settings(workspace_map={"ENG": workspace}))
            with patch("linear_codex_orchestrator.orchestrator.remote_branch_exists", return_value=False):
                with patch("linear_codex_orchestrator.orchestrator.branch_exists", return_value=False):
                    with patch(
                        "linear_codex_orchestrator.orchestrator.ensure_branch",
                        lambda *args: ensured.append(args),
                    ):
                        orchestrator.checkout_existing_branch_from_origin(workspace, branch)

        self.assertEqual(ensured, [(repo_path, "develop", branch)])

    def test_linear_feedback_reports_recreate_failure_for_missing_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            branch = "codex/eng-1-review-me"

            workspace = WorkspaceConfig(
                path=repo_path,
                repos={"web": RepoConfig("acme/web", repo_path, "develop")},
            )
            orchestrator = Orchestrator(Settings(workspace_map={"ENG": workspace}))
            with patch("linear_codex_orchestrator.orchestrator.remote_branch_exists", return_value=False):
                with patch("linear_codex_orchestrator.orchestrator.branch_exists", return_value=False):
                    with patch("linear_codex_orchestrator.orchestrator.ensure_branch", side_effect=RuntimeError):
                        with self.assertRaisesRegex(RuntimeError, "Cannot refresh branch"):
                            orchestrator.checkout_existing_branch_from_origin(workspace, branch)

    def test_resume_reports_checkout_failure_separately_from_missing_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            branch = "codex/eng-1-resume-me"

            def fake_branch_exists(path: Path, branch_arg: str) -> bool:
                self.assertEqual(path, repo_path)
                self.assertEqual(branch_arg, branch)
                return True

            def fake_checkout_branch(path: Path, branch_arg: str) -> bool:
                self.assertEqual(path, repo_path)
                self.assertEqual(branch_arg, branch)
                return False

            workspace = WorkspaceConfig(
                path=repo_path,
                repos={"web": RepoConfig("acme/web", repo_path, "develop")},
            )
            orchestrator = Orchestrator(Settings(workspace_map={"ENG": workspace}))
            with patch("linear_codex_orchestrator.orchestrator.branch_exists", fake_branch_exists):
                with patch("linear_codex_orchestrator.orchestrator.checkout_branch", fake_checkout_branch):
                    with self.assertRaisesRegex(RuntimeError, "could not be checked out in: web"):
                        orchestrator.checkout_existing_branch(workspace, branch)

    def test_planner_block_detection_requires_blocked_prefix(self) -> None:
        self.assertTrue(planner_is_blocked("BLOCKED: missing acceptance criteria"))
        self.assertFalse(planner_is_blocked("This task is not blocked by repository context."))

    def test_planner_blocked_comment_surfaces_reason_and_retry_action(self) -> None:
        plan = "BLOCKED: missing acceptance criteria\n\nNeed expected behavior."
        self.assertEqual(planner_block_reason(plan), "missing acceptance criteria")
        comment = planner_blocked_comment(plan)
        self.assertIn("Reason: missing acceptance criteria", comment)
        self.assertIn("remove the `agent-blocked` label to retry", comment)

    def test_planner_prompt_does_not_block_only_for_unreadable_attachment(self) -> None:
        prompt = render_prompt(
            "planner.md",
            issue_identifier="ENG-1",
            issue_context="Issue asks to add section 123 and file splitting config.",
            full_issue_context="Linear says add `SLM.screen.123` exactly.",
        )
        self.assertIn("Do not block solely because a Linear attachment cannot be read", prompt)
        self.assertIn("SLM.screen.123", prompt)
        self.assertIn("use the configured Linear MCP tools to read issue", prompt)

    def test_reviewer_prompt_requires_direct_linear_read(self) -> None:
        prompt = render_prompt("reviewer.md", **{
            "issue_identifier": "ENG-1",
            "issue_title": "Test issue",
            "issue_context": "Linear says add `SLM.screen.123` exactly.",
            "plan": "Do the smallest useful thing.",
            "changed_repos": "- api: /tmp/api",
            "test_instruction": "Run tests.",
        })
        self.assertIn("Before reviewing, use the configured Linear MCP tools", prompt)

    def test_start_comment_lists_repo_paths_not_parent_workspace(self) -> None:
        issue = parse_linear_issue(
            {
                "id": "abc",
                "identifier": "ENG-1",
                "title": "Ship it",
                "description": "",
                "url": "https://linear.app/acme/issue/ENG-1",
                "state": {"name": "Todo"},
                "team": {"key": "ENG", "name": "Engineering"},
                "labels": {"nodes": []},
            }
        )
        comment = start_comment(
            issue,
            WorkspaceConfig(
                path=Path("/tmp/workspace"),
                repos={"web": RepoConfig("acme/web", Path("/tmp/workspace/web"), "develop")},
            ),
            "codex/eng-1-ship-it",
        )
        self.assertNotIn("Workspace:", comment)
        self.assertIn("at `/tmp/workspace/web`", comment)


if __name__ == "__main__":
    unittest.main()
