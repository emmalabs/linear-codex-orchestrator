from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from linear_codex_orchestrator.config import Settings
from linear_codex_orchestrator.git_ops import branch_name
from linear_codex_orchestrator.models import parse_linear_issue
from linear_codex_orchestrator.locks import lock_for_repo


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


if __name__ == "__main__":
    unittest.main()
