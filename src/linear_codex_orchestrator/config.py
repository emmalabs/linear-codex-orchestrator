from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path(".orchestrator")
CONFIG_PATH = CONFIG_DIR / "config.db"
CONFIG_KEY = "settings"


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
    blocked_label: str = "agent-blocked"
    todo_status: str = "Todo"
    in_progress_status: str = "In Progress"
    in_review_status: str = "In Review"
    max_issues_per_tick: int = 1
    lock_dir: Path = Path(".locks")
    dry_run: bool = False
    test_command: str | None = None
    codex_model: str | None = None
    codex_reasoning_effort: str | None = None
    codex_fast_mode: bool = False
    codex_sandbox: str = "workspace-write"
    pr_feedback_branch_prefix: str = "codex/"
    linear_api_key: str | None = None
    hot_reload_config: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        config = read_config_file()
        workspace_map_data = config.get("workspace_map")
        if workspace_map_data is None:
            workspace_map_raw = os.getenv("WORKSPACE_MAP_JSON") or os.getenv("REPO_MAP_JSON", "{}")
            workspace_map_data = json.loads(workspace_map_raw)
        workspace_map = parse_workspace_map(workspace_map_data)
        validate_workspace_map(workspace_map)
        return cls(
            workspace_map=workspace_map,
            auth_mode=str(config_value(config, "AUTH_MODE", "auth_mode", "local")),
            ready_label=optional_str(config_value(config, "LINEAR_READY_LABEL", "ready_label", None)),
            running_label=str(config_value(config, "LINEAR_RUNNING_LABEL", "running_label", "agent-running")),
            blocked_label=str(config_value(config, "LINEAR_BLOCKED_LABEL", "blocked_label", "agent-blocked")),
            todo_status=str(config_value(config, "LINEAR_TODO_STATUS", "todo_status", "Todo")),
            in_progress_status=str(
                config_value(config, "LINEAR_IN_PROGRESS_STATUS", "in_progress_status", "In Progress")
            ),
            in_review_status=str(config_value(config, "LINEAR_IN_REVIEW_STATUS", "in_review_status", "In Review")),
            max_issues_per_tick=int(config_value(config, "MAX_ISSUES_PER_TICK", "max_issues_per_tick", 1)),
            lock_dir=Path(str(config_value(config, "LOCK_DIR", "lock_dir", ".locks"))),
            dry_run=bool_value(config_value(config, "DRY_RUN", "dry_run", False)),
            test_command=optional_str(config_value(config, "TEST_COMMAND", "test_command", None)),
            codex_model=optional_str(config_value(config, "CODEX_MODEL", "codex_model", None)),
            codex_reasoning_effort=optional_str(
                config_value(config, "CODEX_REASONING_EFFORT", "codex_reasoning_effort", None)
            ),
            codex_fast_mode=bool_value(config_value(config, "CODEX_FAST_MODE", "codex_fast_mode", False)),
            codex_sandbox=str(config_value(config, "CODEX_SANDBOX", "codex_sandbox", "workspace-write")),
            pr_feedback_branch_prefix=str(
                config_value(config, "PR_FEEDBACK_BRANCH_PREFIX", "pr_feedback_branch_prefix", "codex/")
            ),
            linear_api_key=optional_str(config_value(config, "LINEAR_API_KEY", "linear_api_key", None)),
            hot_reload_config=bool_value(config_value(config, "HOT_RELOAD_CONFIG", "hot_reload_config", True)),
        )


def config_path() -> Path:
    return Path(os.getenv("ORCHESTRATOR_CONFIG_PATH", str(CONFIG_PATH))).expanduser()


def read_config_file(path: Path | None = None) -> dict[str, object]:
    target = path or config_path()
    try:
        payload = read_config_from_db(target)
    except sqlite3.Error as exc:
        raise RuntimeError(f"Invalid orchestrator config database: {target}") from exc
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid orchestrator config database: {target}")
    return payload


def write_config_file(payload: dict[str, object], path: Path | None = None) -> None:
    target = path or config_path()
    normalized = normalize_config_payload(payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_config_to_db(target, normalized)


def read_config_from_db(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    with sqlite3.connect(path) as connection:
        ensure_config_schema(connection)
        row = connection.execute("select value from app_config where key = ?", (CONFIG_KEY,)).fetchone()
    if row is None:
        return None
    payload = json.loads(str(row[0]))
    return payload if isinstance(payload, dict) else None


def write_config_to_db(path: Path, payload: dict[str, object]) -> None:
    with sqlite3.connect(path) as connection:
        ensure_config_schema(connection)
        connection.execute(
            """
            insert into app_config(key, value, updated_at)
            values (?, ?, datetime('now'))
            on conflict(key) do update set
              value = excluded.value,
              updated_at = excluded.updated_at
            """,
            (CONFIG_KEY, json.dumps(payload, sort_keys=True)),
        )


def ensure_config_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        create table if not exists app_config (
          key text primary key,
          value text not null,
          updated_at text not null default (datetime('now'))
        )
        """
    )


def normalize_config_payload(payload: dict[str, object]) -> dict[str, object]:
    workspace_map = parse_workspace_map(payload.get("workspace_map") or {})
    validate_workspace_map(workspace_map)
    normalized: dict[str, object] = {"workspace_map": workspace_map_to_json(workspace_map)}
    for key in (
        "auth_mode",
        "ready_label",
        "running_label",
        "blocked_label",
        "todo_status",
        "in_progress_status",
        "in_review_status",
        "lock_dir",
        "test_command",
        "codex_model",
        "codex_reasoning_effort",
        "codex_sandbox",
        "pr_feedback_branch_prefix",
        "linear_api_key",
    ):
        value = optional_str(payload.get(key))
        if value is not None:
            normalized[key] = value
    normalized["max_issues_per_tick"] = int(payload.get("max_issues_per_tick") or 1)
    normalized["dry_run"] = bool_value(payload.get("dry_run", False))
    normalized["codex_fast_mode"] = bool_value(payload.get("codex_fast_mode", False))
    normalized["hot_reload_config"] = bool_value(payload.get("hot_reload_config", True))
    return normalized


def config_value(config: dict[str, object], env_key: str, config_key: str, default: object) -> object:
    if config_key in config:
        return config[config_key]
    if env_key in os.environ:
        return os.environ[env_key]
    return default


def config_payload_from_env() -> dict[str, object]:
    payload: dict[str, object] = {}
    workspace_map_raw = os.getenv("WORKSPACE_MAP_JSON") or os.getenv("REPO_MAP_JSON")
    if workspace_map_raw:
        payload["workspace_map"] = workspace_map_to_json(parse_workspace_map(json.loads(workspace_map_raw)))
    env_to_config = {
        "AUTH_MODE": "auth_mode",
        "LINEAR_READY_LABEL": "ready_label",
        "LINEAR_RUNNING_LABEL": "running_label",
        "LINEAR_BLOCKED_LABEL": "blocked_label",
        "LINEAR_TODO_STATUS": "todo_status",
        "LINEAR_IN_PROGRESS_STATUS": "in_progress_status",
        "LINEAR_IN_REVIEW_STATUS": "in_review_status",
        "MAX_ISSUES_PER_TICK": "max_issues_per_tick",
        "LOCK_DIR": "lock_dir",
        "DRY_RUN": "dry_run",
        "TEST_COMMAND": "test_command",
        "CODEX_MODEL": "codex_model",
        "CODEX_REASONING_EFFORT": "codex_reasoning_effort",
        "CODEX_FAST_MODE": "codex_fast_mode",
        "CODEX_SANDBOX": "codex_sandbox",
        "PR_FEEDBACK_BRANCH_PREFIX": "pr_feedback_branch_prefix",
        "LINEAR_API_KEY": "linear_api_key",
        "HOT_RELOAD_CONFIG": "hot_reload_config",
    }
    for env_key, config_key in env_to_config.items():
        if env_key not in os.environ:
            continue
        value: object = os.environ[env_key]
        if config_key in {"dry_run", "codex_fast_mode", "hot_reload_config"}:
            value = bool_value(value)
        elif config_key == "max_issues_per_tick":
            value = int(str(value))
        payload[config_key] = value
    return payload


def optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).lower() in {"1", "true", "yes", "on"}


def parse_workspace_map(data: object) -> dict[str, WorkspaceConfig]:
    if not isinstance(data, dict):
        raise RuntimeError("workspace_map must be a JSON object")
    return {
        str(key).upper(): WorkspaceConfig(
            path=Path(str(value["path"])).expanduser(),
            repos={
                str(repo_key): RepoConfig(
                    github=str(repo_value["github"]),
                    path=Path(str(repo_value["path"])).expanduser(),
                    base=str(repo_value.get("base", "main")),
                )
                for repo_key, repo_value in dict(value.get("repos", {})).items()
            },
        )
        for key, value in data.items()
        if isinstance(value, dict)
    }


def workspace_map_to_json(workspace_map: dict[str, WorkspaceConfig]) -> dict[str, object]:
    return {
        team_key: {
            "path": str(workspace.path),
            "repos": {
                repo_key: {
                    "github": repo.github,
                    "path": str(repo.path),
                    "base": repo.base,
                }
                for repo_key, repo in workspace.repos.items()
            },
        }
        for team_key, workspace in workspace_map.items()
    }


def validate_workspace_map(workspace_map: dict[str, WorkspaceConfig]) -> None:
    missing: list[str] = []
    for team_key, workspace in workspace_map.items():
        if not workspace.path.is_dir():
            missing.append(f"{team_key} workspace path does not exist: {workspace.path}")
        for repo_key, repo in workspace.repos.items():
            if not repo.path.is_dir():
                missing.append(f"{team_key}.{repo_key} repo path does not exist: {repo.path}")
            elif not (repo.path / ".git").exists():
                missing.append(f"{team_key}.{repo_key} repo path is not a git repository: {repo.path}")
    if missing:
        details = "\n".join(f"- {item}" for item in missing)
        raise RuntimeError(f"Invalid WORKSPACE_MAP_JSON paths:\n{details}")
