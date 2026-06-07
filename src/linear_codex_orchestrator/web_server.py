from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import subprocess
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .config import (
    bool_value,
    config_payload_from_env,
    config_path,
    normalize_config_payload,
    optional_str,
    read_config_file,
    write_config_file,
)
from .linear_api_client import LinearApiClient
from .local_linear_client import LocalLinearClient
from .models import LinearTeam
from .log_summary import read_or_create_log_summary


LOG_DIR = Path(".logs")
FRONTEND_DIST = Path("frontend/dist")
LINEAR_TEAMS_MCP_TIMEOUT_SECONDS = 20


def serve_logs(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), LogRequestHandler)
    print(f"Log UI: http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return
    finally:
        server.server_close()


def start_log_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer | None:
    try:
        server = ThreadingHTTPServer((host, port), LogRequestHandler)
    except OSError as exc:
        print(f"Log UI unavailable on http://{host}:{port}: {exc}", flush=True)
        return None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    bound_host, bound_port = server.server_address
    print(f"Log UI: http://{bound_host}:{bound_port}", flush=True)
    return server


class LogRequestHandler(BaseHTTPRequestHandler):
    server_version = "LinearCodexLogUI/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._send_frontend_index()
            return
        if path == "/api/browse":
            self._send_json(browse_index(parse_qs(parsed.query).get("path", [""])[0]))
            return
        if path == "/api/config":
            self._send_json(config_index())
            return
        if path == "/api/github/repos":
            self._send_json(github_repo_index())
            return
        if path == "/api/logs":
            self._send_json(log_index())
            return
        if path == "/api/tasks":
            self._send_json(task_index())
            return
        if path == "/api/orchestrator":
            self._send_json({"text": tail_text(LOG_DIR / "orchestrator.log", 40000)})
            return
        if path == "/api/status":
            self._send_json(status_index())
            return
        if path.startswith("/logs/"):
            self._send_log(path.removeprefix("/logs/"))
            return
        if self._send_frontend_asset(path):
            return
        if not Path(path).name or "." not in Path(path).name:
            self._send_frontend_index()
            return
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/config":
            self._write_config()
            return
        if path == "/api/linear/teams":
            self._linear_teams()
            return
        if path == "/api/status/archive":
            self._archive_status()
            return
        if path == "/api/status/update":
            self._update_status()
            return
        self.send_error(404)

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send_frontend_index(self) -> None:
        index_path = FRONTEND_DIST / "index.html"
        if not index_path.is_file():
            self._send_html(render_missing_frontend())
            return
        self._send_file(index_path, "text/html; charset=utf-8")

    def _send_frontend_asset(self, raw_path: str) -> bool:
        try:
            asset_path = safe_frontend_path(raw_path)
        except ValueError:
            return False
        if not asset_path.is_file():
            return False
        content_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
        self._send_file(asset_path, content_type)
        return True

    def _send_html(self, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, value: object) -> None:
        payload = json.dumps(value, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_error_json(self, status: int, message: str) -> None:
        payload = json.dumps({"ok": False, "error": message}, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _write_config(self) -> None:
        try:
            payload = self._read_json_body()
            if not isinstance(payload, dict):
                raise ValueError("Config payload must be a JSON object.")
            normalized = normalize_config_payload(payload)
            write_config_file(normalized)
        except (json.JSONDecodeError, KeyError, RuntimeError, ValueError) as exc:
            self._send_error_json(400, str(exc))
            return
        self._send_json({"ok": True, "config": config_index()})

    def _linear_teams(self) -> None:
        try:
            payload = self._read_json_body()
        except (json.JSONDecodeError, ValueError) as exc:
            self._send_json(linear_teams_error("none", str(exc)))
            return
        self._send_json(linear_teams_index(payload))

    def _read_json_body(self) -> object:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1024 * 1024:
            raise ValueError("Config payload is too large.")
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _archive_status(self) -> None:
        try:
            payload = self._read_json_body()
            if not isinstance(payload, dict):
                raise ValueError("Archive payload must be a JSON object.")
            kind = payload.get("kind")
            key = payload.get("key")
            if kind not in {"issue", "pr"}:
                raise ValueError('Archive kind must be "issue" or "pr".')
            if not isinstance(key, str) or not key.strip():
                raise ValueError("Archive key is required.")
            archived = archive_status_item(kind, key.strip())
        except (json.JSONDecodeError, RuntimeError, ValueError) as exc:
            self._send_error_json(400, str(exc))
            return
        self._send_json({"ok": True, "archived": archived, "status": status_index()})

    def _update_status(self) -> None:
        try:
            payload = self._read_json_body()
            if not isinstance(payload, dict):
                raise ValueError("Status payload must be a JSON object.")
            kind = payload.get("kind")
            key = payload.get("key")
            status = payload.get("status")
            if kind not in {"issue", "pr"}:
                raise ValueError('Status kind must be "issue" or "pr".')
            if not isinstance(key, str) or not key.strip():
                raise ValueError("Status key is required.")
            if not isinstance(status, str) or not status.strip():
                raise ValueError("Status value is required.")
            updated = update_status_item(kind, key.strip(), status.strip())
        except (json.JSONDecodeError, RuntimeError, ValueError) as exc:
            self._send_error_json(400, str(exc))
            return
        self._send_json({"ok": True, "updated": updated, "status": status_index()})

    def _send_log(self, raw_name: str) -> None:
        try:
            log_path = safe_log_path(raw_name)
        except ValueError:
            self.send_error(404)
            return
        if not log_path.is_file():
            self.send_error(404)
            return
        self._send_file(log_path, "text/plain; charset=utf-8")

    def _send_file(self, path: Path, content_type: str) -> None:
        payload = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def config_index() -> dict[str, object]:
    path = config_path()
    config = read_config_file(path)
    source = "sqlite"
    if not config:
        config = config_payload_from_env()
        source = "environment" if config else "defaults"
    return {
        "path": str(path),
        "exists": path.is_file(),
        "source": source,
        "config": config,
    }


def linear_teams_index(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return linear_teams_error("none", "Team lookup payload must be a JSON object.")
    config = merged_config_payload()
    request_key = optional_str(payload.get("linear_api_key"))
    saved_key = optional_str(config.get("linear_api_key"))
    api_key = request_key or saved_key
    if api_key:
        try:
            client = LinearApiClient(api_key)
            teams = asyncio.run(client.teams())
            return {"ok": True, "source": "api", "teams": linear_teams_to_json(teams)}
        except Exception as exc:
            return linear_teams_error("api", str(exc))

    try:
        client = LocalLinearClient(
            Path.cwd(),
            model=optional_str(payload.get("codex_model")) or optional_str(config.get("codex_model")),
            reasoning_effort=optional_str(payload.get("codex_reasoning_effort"))
            or optional_str(config.get("codex_reasoning_effort")),
            fast_mode=bool_value(payload.get("codex_fast_mode", config.get("codex_fast_mode", False))),
        )
        teams = asyncio.run(client.teams(timeout_seconds=LINEAR_TEAMS_MCP_TIMEOUT_SECONDS))
        return {"ok": True, "source": "mcp", "teams": linear_teams_to_json(teams)}
    except Exception as exc:
        return linear_teams_error("mcp", str(exc))


def merged_config_payload() -> dict[str, object]:
    config = config_payload_from_env()
    config.update(read_config_file())
    return config


def linear_teams_to_json(teams: list[LinearTeam]) -> list[dict[str, str]]:
    return [{"id": team.id, "key": team.key, "name": team.name} for team in teams]


def linear_teams_error(source: str, error: str) -> dict[str, object]:
    return {"ok": False, "source": source, "error": error, "teams": []}


def browse_index(raw_path: str) -> dict[str, object]:
    current = Path(raw_path).expanduser() if raw_path else Path.home()
    if not current.exists() or not current.is_dir():
        current = current.parent if current.parent.exists() else Path.home()
    current = current.resolve()
    directories: list[dict[str, str]] = []
    repositories: list[dict[str, object]] = []
    try:
        for child in sorted(current.iterdir(), key=lambda item: item.name.lower()):
            if child.is_dir():
                directories.append({"name": child.name, "path": str(child)})
                if (child / ".git").exists():
                    repositories.append(repo_info(child))
    except OSError:
        directories = []
    return {
        "path": str(current),
        "parent": str(current.parent) if current.parent != current else None,
        "directories": directories,
        "current_repository": repo_info(current) if (current / ".git").exists() else None,
        "repositories": repositories,
    }


def repo_info(path: Path) -> dict[str, object]:
    info = {
        "key": repo_key_from_path(path),
        "path": str(path.resolve()),
        "github": github_repo_from_remote(path),
        "base": git_default_branch(path),
    }
    branches = git_branches(path)
    if branches:
        info["branches"] = branches
    return info


def repo_key_from_path(path: Path) -> str:
    name = path.name
    if name.endswith(".git"):
        name = name[:-4]
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-").lower() or "repo"


def github_repo_from_remote(path: Path) -> str | None:
    try:
        remote = subprocess.run(
            ["git", "-C", str(path), "remote", "get-url", "origin"],
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    patterns = (
        r"github\.com[:/](?P<repo>[^/\s]+/[^/\s]+?)(?:\.git)?$",
        r"^https?://[^/]+/(?P<repo>[^/\s]+/[^/\s]+?)(?:\.git)?$",
    )
    for pattern in patterns:
        match = re.search(pattern, remote)
        if match:
            return match.group("repo")
    return None


def git_default_branch(path: Path) -> str:
    commands = (
        ["git", "-C", str(path), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        ["git", "-C", str(path), "branch", "--show-current"],
    )
    for command in commands:
        try:
            value = subprocess.run(
                command,
                capture_output=True,
                check=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
        if value:
            return value.removeprefix("origin/")
    return "main"


def git_branches(path: Path) -> list[str]:
    try:
        output = subprocess.run(
            ["git", "-C", str(path), "branch", "--all", "--format=%(refname:short)"],
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    branches: list[str] = []
    for line in output.splitlines():
        branch = line.strip().removeprefix("remotes/")
        if branch.endswith("/HEAD"):
            continue
        branch = branch.removeprefix("origin/")
        if branch and branch not in branches:
            branches.append(branch)
    return branches


def github_repo_index() -> dict[str, object]:
    command = [
        "gh",
        "api",
        "--paginate",
        "/user/repos?per_page=100&type=all&sort=full_name",
        "--jq",
        (
            '.[] | {nameWithOwner:.full_name, permission:'
            '(if .permissions.admin then "ADMIN" '
            'elif .permissions.push then "WRITE" '
            'elif .permissions.pull then "READ" else "UNKNOWN" end)}'
        ),
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc), "repos": []}
    repos: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        try:
            repo = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(repo, dict) and repo.get("nameWithOwner"):
            repos.append(
                {
                    "nameWithOwner": str(repo["nameWithOwner"]),
                    "permission": str(repo.get("permission") or "UNKNOWN"),
                }
            )
    repos.sort(key=lambda item: item["nameWithOwner"].lower())
    return {"ok": True, "repos": repos}


def safe_log_path(raw_name: str) -> Path:
    name = unquote(raw_name)
    if "/" in name or "\\" in name or name in {"", ".", ".."}:
        raise ValueError(name)
    path = (LOG_DIR / name).resolve()
    log_dir = LOG_DIR.resolve()
    if log_dir != path.parent:
        raise ValueError(name)
    return path


def safe_frontend_path(raw_path: str) -> Path:
    path = (FRONTEND_DIST / unquote(raw_path).lstrip("/")).resolve()
    frontend_dir = FRONTEND_DIST.resolve()
    if frontend_dir != path and frontend_dir not in path.parents:
        raise ValueError(raw_path)
    return path


def log_index() -> list[dict[str, object]]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    for path in sorted(LOG_DIR.glob("*.log"), key=lambda item: item.stat().st_mtime, reverse=True):
        stat = path.stat()
        entries.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "modified": int(stat.st_mtime),
                "kind": "orchestration" if path.name == "orchestrator.log" else "stage",
                "summary": None if path.name == "orchestrator.log" else read_or_create_log_summary(path),
            }
        )
    return entries


def task_index() -> list[dict[str, object]]:
    tasks: dict[str, dict[str, object]] = {}
    for entry in log_index():
        if entry["kind"] != "stage":
            continue
        name = str(entry["name"])
        task = task_from_log_name(name)
        current = tasks.setdefault(
            task["key"],
            {
                **task,
                "modified": 0,
                "log_count": 0,
                "file_count": 0,
                "tokens_used": 0.0,
                "stages": [],
            },
        )
        stages = current["stages"]
        assert isinstance(stages, list)
        stages.append(entry)
        current["modified"] = max(int(current["modified"]), int(entry["modified"]))
        current["log_count"] = int(current["log_count"]) + 1
        summary = entry.get("summary")
        if isinstance(summary, dict):
            current["file_count"] = int(current["file_count"]) + int(summary.get("file_count") or 0)
            tokens = summary.get("tokens_used")
            if isinstance(tokens, (int, float)):
                current["tokens_used"] = float(current["tokens_used"]) + float(tokens)
            if summary.get("headline") and not current.get("headline"):
                current["headline"] = summary["headline"]
    for task in tasks.values():
        stages = task["stages"]
        assert isinstance(stages, list)
        stages.sort(key=lambda item: int(item["modified"]), reverse=True)
    return sorted(tasks.values(), key=lambda item: int(item["modified"]), reverse=True)


def task_from_log_name(name: str) -> dict[str, str]:
    stem = Path(name).stem
    match = re.match(r"^\d{8}-\d{6}-(?P<body>.+)$", stem)
    body = match.group("body") if match else stem
    stage_names = (
        "implementation",
        "optimization",
        "planner",
        "pr-feedback",
        "review-fix",
        "review",
    )
    stage = "stage"
    task_key = body
    for candidate in sorted(stage_names, key=len, reverse=True):
        suffix = f"-{candidate}"
        if body.endswith(suffix):
            stage = candidate
            task_key = body[: -len(suffix)]
            break
    task_type = "PR feedback" if stage == "pr-feedback" else "Linear issue"
    title = task_key.upper() if re.match(r"^[a-z]+-\d+$", task_key) else task_key
    return {
        "key": task_key,
        "title": title,
        "type": task_type,
    }


def status_index() -> dict[str, object]:
    payload = read_status_payload()
    issues = payload.get("issues", {})
    prs = payload.get("prs", {})
    issue_values = list(issues.values()) if isinstance(issues, dict) else []
    pr_values = list(prs.values()) if isinstance(prs, dict) else []
    return {
        "issues": sorted_status_items(item for item in issue_values if not is_archived_status_item(item)),
        "prs": sorted_status_items(item for item in pr_values if not is_archived_status_item(item)),
        "archived_issues": sorted_status_items(item for item in issue_values if is_archived_status_item(item)),
        "archived_prs": sorted_status_items(item for item in pr_values if is_archived_status_item(item)),
    }


def archive_status_item(kind: str, key: str) -> bool:
    payload = read_status_payload()
    collection_key = "issues" if kind == "issue" else "prs"
    collection = payload.get(collection_key, {})
    if not isinstance(collection, dict):
        collection = {}
        payload[collection_key] = collection
    current = collection.get(key)
    existed = isinstance(current, dict) and not current.get("archived")
    if existed:
        current["archived"] = True
        current["archived_at"] = datetime.now().isoformat(timespec="seconds")
    write_status_payload(payload)
    return existed


def update_status_item(kind: str, key: str, status: str) -> bool:
    payload = read_status_payload()
    collection_key = "issues" if kind == "issue" else "prs"
    collection = payload.get(collection_key, {})
    if not isinstance(collection, dict):
        collection = {}
        payload[collection_key] = collection
    current = collection.get(key)
    if not isinstance(current, dict):
        return False
    current["status"] = status
    current["updated_at"] = datetime.now().isoformat(timespec="seconds")
    write_status_payload(payload)
    return True


def sorted_status_items(items: object) -> list[object]:
    return sorted(
        items,
        key=lambda item: str(item.get("archived_at") or item.get("updated_at", "")) if isinstance(item, dict) else "",
        reverse=True,
    )


def is_archived_status_item(item: object) -> bool:
    return isinstance(item, dict) and bool(item.get("archived"))


def read_status_payload() -> dict[str, object]:
    try:
        with (LOG_DIR / "status.json").open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        payload = {}
    issues = payload.get("issues", {}) if isinstance(payload.get("issues", {}), dict) else {}
    prs = payload.get("prs", {}) if isinstance(payload.get("prs", {}), dict) else {}
    legacy_archived_prs = payload.get("archived_prs", {})
    if isinstance(legacy_archived_prs, dict):
        for key, value in legacy_archived_prs.items():
            if isinstance(value, dict) and key not in prs:
                prs[key] = {**value, "archived": True}
    return {
        "issues": issues,
        "prs": prs,
    }


def write_status_payload(payload: dict[str, object]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "status.json").open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def render_missing_frontend() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Linear Codex Orchestrator</title>
</head>
<body>
  <main>
    <h1>Linear Codex Orchestrator</h1>
    <p>The React dashboard has not been built yet.</p>
    <pre>npm --prefix frontend install
npm --prefix frontend run build</pre>
  </main>
</body>
</html>"""


def tail_text(path: Path, max_chars: int) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]
