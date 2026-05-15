from __future__ import annotations

import subprocess
from pathlib import Path

from agents import function_tool


def build_reviewer_tools(repo_path: Path, test_command: str | None):
    @function_tool
    def git_status() -> str:
        """Return the current git status in porcelain format."""
        return _run(repo_path, ["git", "status", "--porcelain"])

    @function_tool
    def git_diff() -> str:
        """Return the staged and unstaged diff against HEAD."""
        return _run(repo_path, ["git", "diff", "HEAD", "--"])

    @function_tool
    def run_tests() -> str:
        """Run the configured project test command."""
        if not test_command:
            return "No TEST_COMMAND configured."
        return _run(repo_path, test_command, shell=True)

    return [git_status, git_diff, run_tests]


def _run(repo_path: Path, command: list[str] | str, shell: bool = False) -> str:
    result = subprocess.run(
        command,
        cwd=repo_path,
        shell=shell,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=900,
    )
    return f"exit_code={result.returncode}\n{result.stdout.strip()}"

