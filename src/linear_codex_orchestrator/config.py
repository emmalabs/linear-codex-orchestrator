from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepoConfig:
    github: str
    path: Path
    base: str = "main"


@dataclass(frozen=True)
class WorkspaceConfig:
    path: Path
    repos: dict[str, RepoConfig]


@dataclass(frozen=True)
class Settings:
    workspace_map: dict[str, WorkspaceConfig]
    auth_mode: str = "local"
    ready_label: str | None = None
    running_label: str = "agent-running"
    todo_status: str = "Todo"
    in_progress_status: str = "In Progress"
    in_review_status: str = "In Review"
    max_issues_per_tick: int = 1
    lock_dir: Path = Path(".locks")
    dry_run: bool = True
    test_command: str | None = None
    codex_model: str | None = None
    codex_sandbox: str = "workspace-write"

    @classmethod
    def from_env(cls) -> "Settings":
        workspace_map_raw = os.getenv("WORKSPACE_MAP_JSON") or os.getenv("REPO_MAP_JSON", "{}")
        workspace_map_data = json.loads(workspace_map_raw)
        workspace_map = {
            key.upper(): WorkspaceConfig(
                path=Path(value["path"]).expanduser(),
                repos={
                    repo_key: RepoConfig(
                        github=repo_value["github"],
                        path=Path(repo_value["path"]).expanduser(),
                        base=repo_value.get("base", "main"),
                    )
                    for repo_key, repo_value in value.get("repos", {}).items()
                },
            )
            for key, value in workspace_map_data.items()
        }
        return cls(
            workspace_map=workspace_map,
            auth_mode=os.getenv("AUTH_MODE", "local"),
            ready_label=os.getenv("LINEAR_READY_LABEL") or None,
            running_label=os.getenv("LINEAR_RUNNING_LABEL", "agent-running"),
            todo_status=os.getenv("LINEAR_TODO_STATUS", "Todo"),
            in_progress_status=os.getenv("LINEAR_IN_PROGRESS_STATUS", "In Progress"),
            in_review_status=os.getenv("LINEAR_IN_REVIEW_STATUS", "In Review"),
            max_issues_per_tick=int(os.getenv("MAX_ISSUES_PER_TICK", "1")),
            lock_dir=Path(os.getenv("LOCK_DIR", ".locks")),
            dry_run=os.getenv("DRY_RUN", "true").lower() in {"1", "true", "yes"},
            test_command=os.getenv("TEST_COMMAND") or None,
            codex_model=os.getenv("CODEX_MODEL") or None,
            codex_sandbox=os.getenv("CODEX_SANDBOX", "workspace-write"),
        )
