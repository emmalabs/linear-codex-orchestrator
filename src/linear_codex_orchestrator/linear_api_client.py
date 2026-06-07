from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

from .models import (
    LinearCommentFeedback,
    LinearIssue,
    LinearTeam,
    linear_comment_feedback_key,
    mark_linear_orchestrator_comment,
)


LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"


class LinearApiClient:
    def __init__(self, api_key: str, *, dry_run: bool = False) -> None:
        self._api_key = api_key
        self._dry_run = dry_run

    async def close(self) -> None:
        return None

    async def ready_issues(
        self,
        status: str,
        label: str | None,
        limit: int,
        exclude_labels: tuple[str, ...] = (),
        team_keys: tuple[str, ...] = (),
    ) -> list[LinearIssue]:
        query = """
query ReadyIssues($first: Int!, $filter: IssueFilter) {
  issues(first: $first, filter: $filter) {
    nodes {
      id
      identifier
      title
      description
      url
      team { id key name }
      state { id name }
      labels { nodes { id name } }
      project { id name url }
    }
  }
}
"""
        filter_value: dict[str, object] = {"state": {"name": {"eq": status}}}
        if team_keys:
            filter_value["team"] = {"key": {"in": list(team_keys)}}
        payload = await self._graphql(
            query,
            {
                "first": max(limit * 10, limit, 10),
                "filter": filter_value,
            },
        )
        issues = [
            issue_from_node(node)
            for node in payload["issues"]["nodes"]
            if issue_matches_labels(node, label, exclude_labels)
        ]
        return issues[:limit]

    async def teams(self) -> list[LinearTeam]:
        query = """
query Teams {
  teams(first: 250) {
    nodes {
      id
      key
      name
    }
  }
}
"""
        payload = await self._graphql(query, {})
        return [team_from_node(node) for node in payload["teams"]["nodes"]]

    async def issue_context(self, issue: LinearIssue) -> str:
        query = """
query IssueContext($id: String!) {
  issue(id: $id) {
    id
    identifier
    title
    description
    url
    team { key name }
    state { name }
    labels { nodes { name } }
    project { name url }
    comments(first: 100) {
      nodes {
        createdAt
        body
        user { name displayName }
      }
    }
    attachments(first: 50) {
      nodes {
        title
        url
      }
    }
  }
}
"""
        payload = await self._graphql(query, {"id": issue.id})
        return render_issue_context(payload["issue"])

    async def issue_comments(self, issue: LinearIssue) -> list[LinearCommentFeedback]:
        query = """
query IssueComments($id: String!) {
  issue(id: $id) {
    comments(first: 250) {
      nodes {
        id
        body
        url
        createdAt
        updatedAt
        user { name displayName }
      }
    }
  }
}
"""
        payload = await self._graphql(query, {"id": issue.id})
        comments = [
            linear_comment_from_node(node, issue.url)
            for node in payload["issue"]["comments"]["nodes"]
        ]
        return sorted(comments, key=lambda item: item.created_at)

    async def move_issue(self, issue_id: str, status_name: str) -> None:
        issue = await self._issue_metadata(issue_id)
        state_id = state_id_by_name(issue, status_name)
        if self._dry_run:
            print(f"[dry-run] Linear API: move {issue_id} to {status_name}")
            return
        await self._graphql(
            """
mutation MoveIssue($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) { success }
}
""",
            {"id": issue_id, "input": {"stateId": state_id}},
        )

    async def add_label(self, issue_id: str, label_name: str) -> None:
        issue = await self._issue_metadata(issue_id)
        label_ids = label_ids_from_issue(issue)
        label_id = label_id_by_name(issue, label_name)
        if label_id is None:
            label_id = await self._create_label(issue["team"]["id"], label_name)
        if label_id in label_ids:
            return
        if self._dry_run:
            print(f"[dry-run] Linear API: add label {label_name} to {issue_id}")
            return
        await self._graphql(
            """
mutation AddLabel($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) { success }
}
""",
            {"id": issue_id, "input": {"labelIds": sorted(label_ids | {label_id})}},
        )

    async def remove_label(self, issue_id: str, label_name: str) -> None:
        issue = await self._issue_metadata(issue_id)
        label_ids = label_ids_from_issue(issue)
        label_id = label_id_by_name(issue, label_name)
        if label_id is None or label_id not in label_ids:
            return
        if self._dry_run:
            print(f"[dry-run] Linear API: remove label {label_name} from {issue_id}")
            return
        await self._graphql(
            """
mutation RemoveLabel($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) { success }
}
""",
            {"id": issue_id, "input": {"labelIds": sorted(label_ids - {label_id})}},
        )

    async def comment(self, issue_id: str, body: str) -> None:
        if self._dry_run:
            print(f"[dry-run] Linear API: comment on {issue_id}")
            return
        await self._graphql(
            """
mutation Comment($input: CommentCreateInput!) {
  commentCreate(input: $input) { success }
}
""",
            {"input": {"issueId": issue_id, "body": mark_linear_orchestrator_comment(body)}},
        )

    async def attach_pr(self, issue_id: str, pr_url: str) -> None:
        await self.comment(issue_id, f"Pull request: {pr_url}")

    async def _create_label(self, team_id: str, label_name: str) -> str:
        if self._dry_run:
            return f"dry-run-label-{label_name}"
        payload = await self._graphql(
            """
mutation CreateLabel($input: IssueLabelCreateInput!) {
  issueLabelCreate(input: $input) {
    success
    issueLabel { id }
  }
}
""",
            {"input": {"teamId": team_id, "name": label_name}},
        )
        return payload["issueLabelCreate"]["issueLabel"]["id"]

    async def _issue_metadata(self, issue_id: str) -> dict[str, Any]:
        payload = await self._graphql(
            """
query IssueMetadata($id: String!) {
  issue(id: $id) {
    id
    team {
      id
      states { nodes { id name } }
      labels { nodes { id name } }
    }
    labels { nodes { id name } }
  }
}
""",
            {"id": issue_id},
        )
        return payload["issue"]

    async def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._graphql_sync, query, variables)

    def _graphql_sync(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        request = urllib.request.Request(
            LINEAR_GRAPHQL_URL,
            data=body,
            headers={
                "Authorization": self._api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Linear API HTTP {exc.code}: {details}") from exc
        if payload.get("errors"):
            raise RuntimeError(f"Linear API GraphQL error: {payload['errors']}")
        return payload["data"]


def issue_from_node(node: dict[str, Any]) -> LinearIssue:
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
        project_name=project.get("name") or "",
        project_url=project.get("url") or "",
    )


def team_from_node(node: dict[str, Any]) -> LinearTeam:
    return LinearTeam(
        id=str(node["id"]),
        key=str(node["key"]).upper(),
        name=str(node["name"]),
    )


def linear_comment_from_node(node: dict[str, Any], issue_url: str) -> LinearCommentFeedback:
    updated_at = str(node.get("updatedAt") or node.get("createdAt") or "")
    comment_id = str(node["id"])
    user = node.get("user") or {}
    author = user.get("displayName") or user.get("name") or "unknown"
    return LinearCommentFeedback(
        key=linear_comment_feedback_key(comment_id, updated_at),
        id=comment_id,
        author=str(author),
        body=node.get("body") or "",
        url=node.get("url") or f"{issue_url}#comment-{comment_id}",
        created_at=str(node.get("createdAt") or ""),
        updated_at=updated_at,
    )


def issue_matches_labels(node: dict[str, Any], label: str | None, exclude_labels: tuple[str, ...]) -> bool:
    labels = {item["name"] for item in node["labels"]["nodes"]}
    if label and label not in labels:
        return False
    return not labels.intersection(exclude_labels)


def label_ids_from_issue(issue: dict[str, Any]) -> set[str]:
    return {label["id"] for label in issue["labels"]["nodes"]}


def label_id_by_name(issue: dict[str, Any], label_name: str) -> str | None:
    for label in issue["team"]["labels"]["nodes"]:
        if label["name"] == label_name:
            return label["id"]
    return None


def state_id_by_name(issue: dict[str, Any], status_name: str) -> str:
    for state in issue["team"]["states"]["nodes"]:
        if state["name"] == status_name:
            return state["id"]
    raise RuntimeError(f"Linear status does not exist: {status_name}")


def render_issue_context(issue: dict[str, Any]) -> str:
    labels = ", ".join(label["name"] for label in issue["labels"]["nodes"]) or "none"
    project = issue.get("project") or {}
    lines = [
        f"# {issue['identifier']}: {issue['title']}",
        "",
        f"- URL: {issue['url']}",
        f"- Team: {issue['team']['key']} ({issue['team']['name']})",
        f"- State: {issue['state']['name']}",
        f"- Labels: {labels}",
        f"- Project: {project.get('name') or 'none'}",
        "",
        "## Description",
        "",
        issue.get("description") or "",
        "",
        "## Comments",
        "",
    ]
    comments = issue["comments"]["nodes"]
    if comments:
        for comment in sorted(comments, key=lambda item: item["createdAt"]):
            user = comment.get("user") or {}
            author = user.get("displayName") or user.get("name") or "Unknown"
            lines.extend([f"### {comment['createdAt']} - {author}", "", comment.get("body") or "", ""])
    else:
        lines.extend(["No comments.", ""])
    attachments = issue.get("attachments", {}).get("nodes", [])
    lines.extend(["## Attachments", ""])
    if attachments:
        for attachment in attachments:
            lines.append(f"- {attachment.get('title') or 'Attachment'}: {attachment.get('url') or ''}")
    else:
        lines.append("No attachments.")
    return "\n".join(lines).strip()
