from __future__ import annotations

import json
import os
import pty
import re
import select
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .log_summary import write_log_summary


ISSUES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "identifier": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "url": {"type": "string"},
                    "team_key": {"type": "string"},
                    "team_name": {"type": "string"},
                    "state_name": {"type": "string"},
                    "labels": {"type": "array", "items": {"type": "string"}},
                    "project_name": {"type": "string"},
                    "project_url": {"type": "string"},
                },
                "required": [
                    "id",
                    "identifier",
                    "title",
                    "description",
                    "url",
                    "team_key",
                    "team_name",
                    "state_name",
                    "labels",
                    "project_name",
                    "project_url",
                ],
            },
        }
    },
    "required": ["issues"],
}


MUTATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "success": {"type": "boolean"},
        "message": {"type": "string"},
    },
    "required": ["success", "message"],
}


def run_codex(
    prompt: str,
    cwd: Path,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    fast_mode: bool = False,
    sandbox: str = "workspace-write",
    output_schema: dict[str, Any] | None = None,
    timeout_seconds: int = 3600,
    bypass_approvals: bool = False,
    show_output: bool = False,
    log_output_path: Path | None = None,
) -> str:
    with tempfile.NamedTemporaryFile("w+", suffix=".txt") as output_file:
        schema_file = None
        try:
            if output_schema:
                schema_file = tempfile.NamedTemporaryFile("w+", suffix=".json")
                json.dump(output_schema, schema_file)
                schema_file.flush()
            command = build_codex_command(
                prompt,
                cwd,
                sandbox=sandbox,
                model=model,
                reasoning_effort=reasoning_effort,
                fast_mode=fast_mode,
                output_file_path=Path(output_file.name),
                output_schema_path=Path(schema_file.name) if schema_file else None,
                bypass_approvals=bypass_approvals,
            )
            if log_output_path:
                log_output_path.parent.mkdir(parents=True, exist_ok=True)
                log_output_path.write_text("", encoding="utf-8")
            output = _run_process(command, cwd, timeout_seconds, show_output, log_output_path)
            output_file.seek(0)
            last_message = output_file.read().strip()
            if log_output_path:
                write_log_summary(log_output_path, output.stdout, last_message)
            if output.returncode != 0:
                raise RuntimeError(
                    "codex exec failed with exit code "
                    f"{output.returncode}\n\n{output.stdout.strip()}\n\n{last_message}"
                )
            return last_message
        finally:
            if schema_file:
                schema_file.close()


def build_codex_command(
    prompt: str,
    cwd: Path,
    *,
    sandbox: str,
    model: str | None,
    reasoning_effort: str | None,
    fast_mode: bool,
    output_file_path: Path,
    output_schema_path: Path | None = None,
    bypass_approvals: bool = False,
) -> list[str]:
    command = [
        "codex",
        "exec",
        "-C",
        str(cwd),
        "-s",
        sandbox,
        "--skip-git-repo-check",
        "--output-last-message",
        str(output_file_path),
    ]
    if bypass_approvals:
        command.append("--dangerously-bypass-approvals-and-sandbox")
    if model:
        command.extend(["-m", model])
    if reasoning_effort:
        command.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    if fast_mode:
        command.extend(["-c", 'model_service_tier="priority"'])
    if output_schema_path:
        command.extend(["--output-schema", str(output_schema_path)])
    command.append(prompt)
    return command


class CodexProcessOutput:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


def _run_process(
    command: list[str],
    cwd: Path,
    timeout_seconds: int,
    show_output: bool,
    log_output_path: Path | None = None,
) -> CodexProcessOutput:
    if not show_output:
        return _run_process_hidden(command, cwd, timeout_seconds, log_output_path)
    master_fd, slave_fd = pty.openpty()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        text=False,
        stdout=slave_fd,
        stderr=slave_fd,
        stdin=subprocess.DEVNULL,
        close_fds=True,
    )
    os.close(slave_fd)
    chunks: list[str] = []
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            if time.monotonic() > deadline:
                process.kill()
                returncode = process.wait()
                stdout = "".join(chunks)
                raise RuntimeError(
                    f"codex exec timed out after {timeout_seconds}s\n\n{strip_ansi(stdout).strip()}"
                )
            readable, _, _ = select.select([master_fd], [], [], 0.1)
            if readable:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                text = data.decode(errors="replace")
                chunks.append(text)
                append_log_chunk(log_output_path, text)
                print(text, end="", flush=True)
            if process.poll() is not None:
                while True:
                    readable, _, _ = select.select([master_fd], [], [], 0)
                    if not readable:
                        break
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                    text = data.decode(errors="replace")
                    chunks.append(text)
                    append_log_chunk(log_output_path, text)
                    print(text, end="", flush=True)
                break
        return CodexProcessOutput(process.wait(), "".join(chunks))
    finally:
        os.close(master_fd)


def _run_process_hidden(
    command: list[str],
    cwd: Path,
    timeout_seconds: int,
    log_output_path: Path | None,
) -> CodexProcessOutput:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
    )
    chunks: list[str] = []
    deadline = time.monotonic() + timeout_seconds
    assert process.stdout is not None
    try:
        while True:
            if time.monotonic() > deadline:
                process.kill()
                process.wait()
                stdout = "".join(chunks)
                raise RuntimeError(
                    f"codex exec timed out after {timeout_seconds}s\n\n{strip_ansi(stdout).strip()}"
                )
            readable, _, _ = select.select([process.stdout], [], [], 0.1)
            if readable:
                line = process.stdout.readline()
                if line:
                    chunks.append(line)
                    append_log_chunk(log_output_path, line)
                    continue
            if process.poll() is not None:
                remainder = process.stdout.read()
                if remainder:
                    chunks.append(remainder)
                    append_log_chunk(log_output_path, remainder)
                break
    finally:
        process.stdout.close()
    return CodexProcessOutput(process.wait(), "".join(chunks))


def append_log_chunk(log_output_path: Path | None, chunk: str) -> None:
    if not log_output_path:
        return
    with log_output_path.open("a", encoding="utf-8") as handle:
        handle.write(chunk)
        handle.flush()


def strip_ansi(value: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)


def parse_json_object(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        last_payload: dict[str, Any] | None = None
        for match in re.finditer(r"\{", raw):
            prefix = raw[: match.start()].rstrip()
            if prefix and prefix[-1] in "[{:":
                continue
            try:
                payload, _ = decoder.raw_decode(raw[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                last_payload = payload
        if last_payload is None:
            raise
        return last_payload
