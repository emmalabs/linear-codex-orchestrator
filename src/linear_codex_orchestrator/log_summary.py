from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SUMMARY_VERSION = 5


def summary_path_for(log_path: Path) -> Path:
    return log_path.with_suffix(".summary.json")


def write_log_summary(log_path: Path, raw: str, last_message: str) -> None:
    summary_path = summary_path_for(log_path)
    summary_path.write_text(
        json.dumps(summarize_codex_log(log_path, raw, last_message), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_or_create_log_summary(log_path: Path) -> dict[str, Any]:
    summary_path = summary_path_for(log_path)
    if summary_path.is_file() and summary_path.stat().st_mtime >= log_path.stat().st_mtime:
        try:
            with summary_path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict) and payload.get("summary_version") == SUMMARY_VERSION:
                return payload
        except json.JSONDecodeError:
            pass
    raw = log_path.read_text(encoding="utf-8", errors="replace")
    last_message = extract_last_message(raw)
    summary = summarize_codex_log(log_path, raw, last_message)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def summarize_codex_log(log_path: Path, raw: str, last_message: str) -> dict[str, Any]:
    final_message = last_message.strip() or extract_last_message(raw)
    files = changed_files_from_raw(raw)
    token_count = tokens_used(raw)
    is_running = token_count is None
    return {
        "summary_version": SUMMARY_VERSION,
        "status": "running" if is_running else "complete",
        "headline": "Running. Waiting for Codex final message." if is_running else headline_from_message(final_message),
        "message": "" if is_running else final_message,
        "last_line": last_interesting_line(raw),
        "tokens_used": token_count,
        "files": files,
        "file_count": len(files),
        "raw_size": len(raw.encode("utf-8")),
        "raw_log": log_path.name,
    }


def extract_last_message(raw: str) -> str:
    match = re.search(r"(?:^|\n)tokens used\s*\n[0-9.]+\s*\n(?P<message>.*)\Z", raw, re.DOTALL)
    if match:
        return match.group("message").strip()
    codex_blocks = [block.strip() for block in re.split(r"\ncodex\s*\n", raw) if block.strip()]
    return codex_blocks[-1] if codex_blocks else raw.strip()[-4000:]


def headline_from_message(message: str) -> str:
    for line in message.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        return truncate(stripped, 180)
    return "No final summary captured."


def tokens_used(raw: str) -> float | None:
    matches = re.findall(r"(?:^|\n)tokens used\s*\n([0-9.]+)", raw)
    if not matches:
        return None
    return parse_token_count(matches[-1])


def parse_token_count(value: str) -> float | None:
    groups = value.split(".")
    if len(groups) > 1 and len(groups[-1]) == 3:
        joined = "".join(groups)
        if joined.isdigit():
            return float(joined)
    try:
        return float(value)
    except ValueError:
        return None


def last_interesting_line(raw: str) -> str:
    for line in reversed(raw.splitlines()):
        stripped = strip_ansi(line).strip()
        if not stripped:
            continue
        if stripped in {"codex", "exec"}:
            continue
        return truncate(stripped, 240)
    return ""


def changed_files_from_raw(raw: str) -> list[dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    current: str | None = None
    for line in raw.splitlines():
        diff_match = re.match(r"diff --git a/(.+?) b/(.+)$", line)
        patch_match = re.match(r"\*\*\* (?:Update|Add|Delete) File: (.+)$", line)
        link_matches = re.findall(r"\]\((/[^):]+)(?::\d+)?\)", line)
        if diff_match:
            current = normalize_path(diff_match.group(2))
            files.setdefault(current, {"path": current, "added": 0, "removed": 0})
            continue
        if patch_match:
            current = normalize_path(patch_match.group(1))
            files.setdefault(current, {"path": current, "added": 0, "removed": 0})
            continue
        for link in link_matches:
            normalized = normalize_path(link)
            files.setdefault(normalized, {"path": normalized, "added": 0, "removed": 0})
        if current and line.startswith("+") and not line.startswith("+++"):
            files[current]["added"] += 1
        elif current and line.startswith("-") and not line.startswith("---"):
            files[current]["removed"] += 1
    return sorted(files.values(), key=lambda item: item["path"])


def normalize_path(path: str) -> str:
    parts = Path(path).parts
    source_roots = ("src", "docs", "tests", "packages", "apps", "lib", "scripts", "frontend", "backend")
    for marker in source_roots:
        if marker in parts:
            index = parts.index(marker)
            return "/".join(parts[index:])
    return path


def truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
