from __future__ import annotations

import re
import subprocess
from pathlib import Path


def run_git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return result.stdout.strip()


def branch_name(issue_identifier: str, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48]
    return f"codex/{issue_identifier.lower()}-{slug or 'issue'}"


def ensure_branch(repo_path: Path, base: str, branch: str) -> None:
    run_git(repo_path, "fetch", "origin", base)
    run_git(repo_path, "checkout", "-B", branch, f"origin/{base}")


def checkout_branch(repo_path: Path, branch: str) -> bool:
    try:
        run_git(repo_path, "checkout", branch)
        return True
    except subprocess.CalledProcessError:
        return False


def has_changes(repo_path: Path) -> bool:
    return bool(run_git(repo_path, "status", "--porcelain"))


def has_commits_since_base(repo_path: Path, base: str) -> bool:
    try:
        count = run_git(repo_path, "rev-list", "--count", f"origin/{base}..HEAD")
    except subprocess.CalledProcessError:
        return False
    return int(count or "0") > 0


def commit_all(repo_path: Path, message: str) -> None:
    run_git(repo_path, "add", "-A")
    run_git(repo_path, "commit", "-m", message)


def push_branch(repo_path: Path, branch: str) -> None:
    run_git(repo_path, "push", "-u", "origin", branch)


def changed_files(repo_path: Path) -> str:
    return run_git(repo_path, "status", "--porcelain")
