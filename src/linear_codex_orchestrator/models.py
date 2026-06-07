from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LinearIssue:
    id: str
    identifier: str
    title: str
    description: str
    url: str
    team_key: str
    team_name: str
    state_name: str
    labels: tuple[str, ...]
    project_name: str = ""
    project_url: str = ""


@dataclass(frozen=True)
class LinearTeam:
    id: str
    key: str
    name: str


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    title: str


@dataclass(frozen=True)
class OpenPullRequest:
    repo: str
    number: int
    url: str
    title: str
    head_branch: str
    base_branch: str
    head_sha: str = ""


@dataclass(frozen=True)
class PullRequestFeedback:
    key: str
    kind: str
    author: str
    body: str
    url: str
    path: str | None = None


@dataclass(frozen=True)
class LinearCommentFeedback:
    key: str
    id: str
    author: str
    body: str
    url: str
    created_at: str
    updated_at: str


LINEAR_ORCHESTRATOR_HTML_MARKER = "<!-- linear-codex-orchestrator -->"
LINEAR_ORCHESTRATOR_PLAIN_MARKER = "linear-codex-orchestrator"
LINEAR_ORCHESTRATOR_STATUS_PREFIXES = (
    "Codex started work",
    "Codex plan:",
    "Codex implementation finished",
    "Codex optimization pass finished",
    "Codex reviewer",
    "Codex addressed",
    "PRs ready for review",
    "Planner blocked",
    "Codex orchestration failed",
)


@dataclass(frozen=True)
class PullRequestApproval:
    key: str
    author: str
    submitted_at: str
    url: str
    body: str
    commit_id: str = ""


@dataclass(frozen=True)
class ReviewResult:
    passed: bool
    summary: str
    tests: str


def parse_linear_issue(node: dict[str, Any]) -> LinearIssue:
    project = node.get("project") or {}
    return LinearIssue(
        id=node["id"],
        identifier=node["identifier"],
        title=node["title"],
        description=node.get("description") or "",
        url=node["url"],
        team_key=node["team"]["key"],
        team_name=node["team"]["name"],
        state_name=node["state"]["name"],
        labels=tuple(label["name"] for label in node["labels"]["nodes"]),
        project_name=project.get("name") or node.get("project_name") or "",
        project_url=project.get("url") or node.get("project_url") or "",
    )


def mark_linear_orchestrator_comment(body: str) -> str:
    if has_linear_orchestrator_marker(body):
        return body
    return f"{LINEAR_ORCHESTRATOR_HTML_MARKER}\n{LINEAR_ORCHESTRATOR_PLAIN_MARKER}\n\n{body}".strip()


def has_linear_orchestrator_marker(body: str) -> bool:
    return (
        LINEAR_ORCHESTRATOR_HTML_MARKER in body
        or LINEAR_ORCHESTRATOR_PLAIN_MARKER in body
    )


def is_orchestrator_linear_comment(body: str) -> bool:
    stripped = body.strip()
    if not stripped:
        return True
    if has_linear_orchestrator_marker(stripped):
        return True
    normalized = stripped.lower()
    return any(
        normalized.startswith(prefix.lower())
        for prefix in LINEAR_ORCHESTRATOR_STATUS_PREFIXES
    )


def linear_comment_feedback_key(comment_id: str, updated_at: str) -> str:
    return f"linear-comment:{comment_id}:{updated_at}"
