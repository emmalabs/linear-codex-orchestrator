from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from linear_codex_orchestrator.config import Settings
from linear_codex_orchestrator.git_ops import branch_name
from linear_codex_orchestrator.orchestrator import truncate_text
from linear_codex_orchestrator.models import parse_linear_issue
from linear_codex_orchestrator.locks import lock_for_repo
from linear_codex_orchestrator.prompt_templates import render_prompt


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
            }
        )
        self.assertEqual(issue.description, "")
        self.assertEqual(issue.team_key, "ENG")
        self.assertEqual(issue.labels, ("codex-ready",))

    def test_settings_from_env_parses_repo_map(self) -> None:
        env = {
            "WORKSPACE_MAP_JSON": (
                '{"ENG":{"path":"/tmp/workspace","repos":'
                '{"web":{"github":"acme/web","path":"/tmp/web","base":"develop"}}}}'
            ),
            "DRY_RUN": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertFalse(settings.dry_run)
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
            settings = Settings.from_env()
        self.assertEqual(settings.workspace_map["EMMA"].repos["api"].github, "emmalabs/emma.db-api")

    def test_repo_lock_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = lock_for_repo(Path(tmp), "acme/web")
            second = lock_for_repo(Path(tmp), "acme/web")
            with first as acquired:
                self.assertTrue(acquired.acquired)
                with second as blocked:
                    self.assertFalse(blocked.acquired)

    def test_truncate_text_marks_truncated_content(self) -> None:
        self.assertEqual(truncate_text("short", 10), "short")
        self.assertEqual(truncate_text("0123456789abcdef", 10), "0123456789\n\n...[truncated]")

    def test_render_prompt_loads_markdown_template(self) -> None:
        prompt = render_prompt("implementation.md", **{
            "workspace_path": "/tmp/workspace",
            "issue_identifier": "EMMA-1",
            "issue_title": "Test issue",
            "issue_url": "https://linear.app/example/issue/EMMA-1",
            "plan": "Do the smallest useful thing.",
        })
        self.assertIn("EMMA-1", prompt)
        self.assertIn("/tmp/workspace", prompt)


if __name__ == "__main__":
    unittest.main()
