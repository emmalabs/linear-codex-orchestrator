from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any


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
                ],
            },
        }
    },
    "required": ["issues"],
}


def run_codex(
    prompt: str,
    cwd: Path,
    *,
    model: str | None = None,
    sandbox: str = "workspace-write",
    output_schema: dict[str, Any] | None = None,
    timeout_seconds: int = 3600,
) -> str:
    with tempfile.NamedTemporaryFile("w+", suffix=".txt") as output_file:
        command = [
            "codex",
            "exec",
            "-C",
            str(cwd),
            "-s",
            sandbox,
            "--output-last-message",
            output_file.name,
        ]
        schema_file = None
        try:
            if model:
                command.extend(["-m", model])
            if output_schema:
                schema_file = tempfile.NamedTemporaryFile("w+", suffix=".json")
                json.dump(output_schema, schema_file)
                schema_file.flush()
                command.extend(["--output-schema", schema_file.name])
            command.append(prompt)
            result = subprocess.run(
                command,
                cwd=cwd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                timeout=timeout_seconds,
            )
            if result.returncode != 0:
                output_file.seek(0)
                last_message = output_file.read().strip()
                raise RuntimeError(
                    "codex exec failed with exit code "
                    f"{result.returncode}\n\n{result.stdout.strip()}\n\n{last_message}"
                )
            output_file.seek(0)
            return output_file.read().strip()
        finally:
            if schema_file:
                schema_file.close()


def parse_json_object(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(raw[start : end + 1])
