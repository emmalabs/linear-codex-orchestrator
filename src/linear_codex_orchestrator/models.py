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


@dataclass(frozen=True)
class PullRequestFeedback:
    key: str
    kind: str
    author: str
    body: str
    url: str
    path: str | None = None


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
