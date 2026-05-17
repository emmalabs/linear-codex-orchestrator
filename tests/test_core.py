from __future__ import annotations

import os
import tempfile
import unittest
import asyncio
from pathlib import Path
from unittest.mock import patch

from linear_codex_orchestrator.codex_cli import build_codex_command, parse_json_object
from linear_codex_orchestrator.config import Settings
from linear_codex_orchestrator.config import RepoConfig, WorkspaceConfig, validate_workspace_map
from linear_codex_orchestrator.git_ops import branch_name, has_commits_since_base, run_git
from linear_codex_orchestrator.local_github_client import pull_request_number_from_url
from linear_codex_orchestrator.local_linear_client import LocalLinearClient, is_transient_linear_error
from linear_codex_orchestrator.log_summary import summarize_codex_log, tokens_used, write_log_summary
from linear_codex_orchestrator.orchestrator import (
    Orchestrator,
    codex_log_path,
    implementation_comment,
    log_session_start,
    orchestration_log_path,
    planner_block_reason,
    planner_blocked_comment,
    planner_is_blocked,
    pr_feedback_prompt,
    read_processed_feedback,
    read_status,
    start_comment,
    status_path,
    truncate_text,
    update_issue_status,
    update_pr_status,
    workspace_status_context,
    write_processed_feedback,
)
from linear_codex_orchestrator.models import OpenPullRequest, PullRequestFeedback, parse_linear_issue
from linear_codex_orchestrator.locks import lock_for_repo
from linear_codex_orchestrator.prompt_templates import render_prompt
from linear_codex_orchestrator.web_server import (
    render_missing_frontend,
    log_index,
    safe_frontend_path,
    safe_log_path,
    start_log_server,
    status_index,
    task_from_log_name,
    task_index,
    tail_text,
)


class CoreTests(unittest.TestCase):
    def test_branch_name_is_stable_and_safe(self) -> None:
        self.assertEqual(
            branch_name("ENG-123", "Fix OAuth callback: spaces & symbols!"),
            "codex/eng-123-fix-oauth-callback-spaces-symbols",
        )

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
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("linear_codex_orchestrator.config.validate_workspace_map"):
                settings = Settings.from_env()
        self.assertFalse(settings.dry_run)
        self.assertFalse(settings.codex_fast_mode)
        self.assertEqual(settings.pr_feedback_branch_prefix, "codex/")
        self.assertEqual(settings.workspace_map["ENG"].path, Path("/tmp/workspace"))
        self.assertEqual(settings.workspace_map["ENG"].repos["web"].github, "acme/web")
        self.assertEqual(settings.workspace_map["ENG"].repos["web"].path, Path("/tmp/web"))
        self.assertEqual(settings.workspace_map["ENG"].repos["web"].base, "develop")

    def test_settings_from_env_parses_emma_workspace(self) -> None:
        env = {
            "WORKSPACE_MAP_JSON": (
                '{"EMMA":{"path":"/home/aleix/Projects/emma.db","repos":'
                '{"api":{"github":"emmalabs/emma.db-api",'
                '"path":"/home/aleix/Projects/emma.db/emma-api","base":"develop"}}}}'
            ),
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("linear_codex_orchestrator.config.validate_workspace_map"):
                settings = Settings.from_env()
        self.assertEqual(settings.workspace_map["EMMA"].repos["api"].github, "emmalabs/emma.db-api")

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
            "issue_identifier": "EMMA-1",
            "issue_title": "Test issue",
            "issue_url": "https://linear.app/example/issue/EMMA-1",
            "issue_context": "Linear says add `SLM.screen.123` exactly.",
            "plan": "Do the smallest useful thing.",
        })
        self.assertIn("EMMA-1", prompt)
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
            "issue_identifier": "EMMA-1",
            "issue_title": "Test issue",
            "issue_url": "https://linear.app/example/issue/EMMA-1",
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
            "issue_identifier": "EMMA-1",
            "issue_title": "Test issue",
            "issue_url": "https://linear.app/example/issue/EMMA-1",
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

    def test_pull_request_number_is_parsed_from_created_pr_url(self) -> None:
        self.assertEqual(pull_request_number_from_url("https://github.com/acme/web/pull/42"), 42)
        self.assertEqual(pull_request_number_from_url("https://github.com/acme/web/pull/42#discussion"), 42)
        self.assertEqual(pull_request_number_from_url("https://github.com/acme/web/compare/main...branch"), 0)

    def test_pr_feedback_state_round_trips_processed_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            self.assertEqual(read_processed_feedback(path), set())
            write_processed_feedback(path, {"b", "a"})
            self.assertEqual(read_processed_feedback(path), {"a", "b"})

    def test_codex_log_path_is_stable_and_sanitized(self) -> None:
        path = codex_log_path("EMMA/20", "review fix")
        self.assertEqual(path.parent, Path(".logs"))
        self.assertRegex(path.name, r"^\d{8}-\d{6}-emma-20-review-fix\.log$")

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
        self.assertEqual(summary["summary_version"], 2)

    def test_codex_log_summary_parses_dot_separated_token_thousands(self) -> None:
        self.assertEqual(tokens_used("\ntokens used\n135.878\nDone."), 135878.0)
        self.assertEqual(tokens_used("\ntokens used\n1.234.567\nDone."), 1234567.0)

    def test_codex_log_summary_is_written_next_to_raw_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "stage.log"
            raw = "tokens used\n1\nDone."
            log_path.write_text(raw, encoding="utf-8")
            write_log_summary(log_path, raw, "Done.")
            summary_path = Path(tmp) / "stage.summary.json"
            self.assertTrue(summary_path.is_file())
            self.assertIn("Done.", summary_path.read_text(encoding="utf-8"))

    def test_web_task_index_groups_stage_logs_by_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch("linear_codex_orchestrator.web_server.LOG_DIR", tmp_path):
                (tmp_path / "20260517-090000-emma-75-planner.log").write_text("tokens used\n1\nPlanned.", encoding="utf-8")
                (tmp_path / "20260517-091000-emma-75-review.log").write_text("tokens used\n2\nReviewed.", encoding="utf-8")
                (tmp_path / "20260517-092000-data-48-pr-feedback.log").write_text("tokens used\n3\nFixed PR.", encoding="utf-8")
                tasks = task_index()
        self.assertEqual(tasks[0]["key"], "data-48")
        self.assertEqual(tasks[0]["type"], "PR feedback")
        self.assertEqual(tasks[1]["key"], "emma-75")
        self.assertEqual(tasks[1]["log_count"], 2)
        self.assertEqual(len(tasks[1]["stages"]), 2)

    def test_task_from_log_name_extracts_linear_and_pr_feedback_tasks(self) -> None:
        self.assertEqual(task_from_log_name("20260517-090000-emma-75-review.log")["key"], "emma-75")
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
                    '"updated_at":"2026-01-01T00:00:00"}},'
                    '"prs":{"acme/web#1":{"key":"acme/web#1","status":"Ready","repo_path":"/tmp/workspace/web",'
                    '"updated_at":"2026-01-01T00:00:01"}}}',
                    encoding="utf-8",
                )
                summary = status_index()
        self.assertEqual(summary["issues"][0]["identifier"], "ENG-1")
        self.assertEqual(summary["issues"][0]["project"], "Project X")
        self.assertEqual(summary["prs"][0]["key"], "acme/web#1")
        self.assertEqual(summary["prs"][0]["repo_path"], "/tmp/workspace/web")

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
        raw = 'codex\n{"issues":[]}\nmcp: linear/list_issues completed\ncodex\n{"issues":[{"id":"EMMA-79"}]}'
        self.assertEqual(parse_json_object(raw), {"issues": [{"id": "EMMA-79"}]})

    def test_remove_label_treats_absent_label_as_success(self) -> None:
        calls: list[str] = []

        def fake_run_codex(prompt: str, *_args: object, **_kwargs: object) -> str:
            calls.append(prompt)
            return '{"success":true,"message":"removed"}'

        with patch("linear_codex_orchestrator.local_linear_client.run_codex", fake_run_codex):
            asyncio.run(LocalLinearClient(Path("/tmp/workspace")).remove_label("EMMA-79", "agent-running"))

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
                asyncio.run(client.comment("EMMA-79", "done"))

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
                asyncio.run(client.move_issue("EMMA-79", "Missing"))

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
            asyncio.run(LocalLinearClient(Path("/tmp/workspace")).ready_issues("Todo", None, 1, team_keys=("EMMA",)))

        self.assertIn('team key in "EMMA"', calls[0][0])
        self.assertIn("project_name, project_url", calls[0][0])
        self.assertIn("Do not read local files", calls[0][0])
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

        self.assertEqual(linear.calls, [("In Progress", "agent-running")])
        self.assertEqual(seen, [("ENG-1", True)])

    def test_resume_checks_out_existing_branch_without_resetting_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            branch = "codex/eng-1-resume-me"
            run_commands: list[tuple[str, ...]] = []

            def fake_run_git(path: Path, *args: str) -> str:
                self.assertEqual(path, repo_path)
                run_commands.append(args)
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

        self.assertEqual(run_commands, [("checkout", branch)])

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
            issue_identifier="EMMA-1",
            issue_context="Issue asks to add section 123 and file splitting config.",
            full_issue_context="Linear says add `SLM.screen.123` exactly.",
        )
        self.assertIn("Do not block solely because a Linear attachment cannot be read", prompt)
        self.assertIn("SLM.screen.123", prompt)
        self.assertIn("use the configured Linear MCP tools to read issue", prompt)

    def test_reviewer_prompt_requires_direct_linear_read(self) -> None:
        prompt = render_prompt("reviewer.md", **{
            "issue_identifier": "EMMA-1",
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
