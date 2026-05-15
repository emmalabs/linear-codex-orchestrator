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
    label: str | None = None


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    linear_api_key: str
    github_token: str
    repo_map: dict[str, RepoConfig]
    ready_label: str | None = None
    running_label: str = "agent-running"
    todo_status: str = "Todo"
    in_progress_status: str = "In Progress"
    in_review_status: str = "In Review"
    max_issues_per_tick: int = 1
    lock_dir: Path = Path(".locks")
    dry_run: bool = True
    test_command: str | None = None
    codex_model: str = "gpt-5.2-codex"
    agent_model: str = "gpt-5.4"
    codex_sandbox: str = "workspace-write"

    @classmethod
    def from_env(cls) -> "Settings":
        repo_map_raw = os.getenv("REPO_MAP_JSON", "{}")
        repo_map_data = json.loads(repo_map_raw)
        repo_map = {
            key: RepoConfig(
                github=value["github"],
                path=Path(value["path"]).expanduser(),
                base=value.get("base", "main"),
                label=value.get("label"),
            )
            for key, value in repo_map_data.items()
        }
        return cls(
            openai_api_key=require_env("OPENAI_API_KEY"),
            linear_api_key=require_env("LINEAR_API_KEY"),
            github_token=require_env("GITHUB_TOKEN"),
            repo_map=repo_map,
            ready_label=os.getenv("LINEAR_READY_LABEL") or None,
            running_label=os.getenv("LINEAR_RUNNING_LABEL", "agent-running"),
            todo_status=os.getenv("LINEAR_TODO_STATUS", "Todo"),
            in_progress_status=os.getenv("LINEAR_IN_PROGRESS_STATUS", "In Progress"),
            in_review_status=os.getenv("LINEAR_IN_REVIEW_STATUS", "In Review"),
            max_issues_per_tick=int(os.getenv("MAX_ISSUES_PER_TICK", "1")),
            lock_dir=Path(os.getenv("LOCK_DIR", ".locks")),
            dry_run=os.getenv("DRY_RUN", "true").lower() in {"1", "true", "yes"},
            test_command=os.getenv("TEST_COMMAND") or None,
            codex_model=os.getenv("CODEX_MODEL", "gpt-5.2-codex"),
            agent_model=os.getenv("AGENT_MODEL", "gpt-5.4"),
            codex_sandbox=os.getenv("CODEX_SANDBOX", "workspace-write"),
        )


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
