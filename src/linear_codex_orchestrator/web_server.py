from __future__ import annotations

import json
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .log_summary import read_or_create_log_summary


LOG_DIR = Path(".logs")
FRONTEND_DIST = Path("frontend/dist")


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
        path = urlparse(self.path).path
        if path == "/":
            self._send_frontend_index()
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
            if summary.get("headline"):
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
    try:
        with (LOG_DIR / "status.json").open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        payload = {}
    issues = payload.get("issues", {})
    prs = payload.get("prs", {})
    return {
        "issues": sorted(
            issues.values() if isinstance(issues, dict) else [],
            key=lambda item: str(item.get("updated_at", "")),
            reverse=True,
        ),
        "prs": sorted(
            prs.values() if isinstance(prs, dict) else [],
            key=lambda item: str(item.get("updated_at", "")),
            reverse=True,
        ),
    }


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
