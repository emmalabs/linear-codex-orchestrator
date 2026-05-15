from __future__ import annotations

from typing import Any

import httpx

from .models import LinearIssue, parse_linear_issue


class LinearClient:
    def __init__(self, api_key: str, dry_run: bool = False) -> None:
        self._dry_run = dry_run
        self._client = httpx.AsyncClient(
            base_url="https://api.linear.app/graphql",
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            timeout=30,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post("", json={"query": query, "variables": variables})
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(payload["errors"])
        return payload["data"]

    async def ready_issues(
        self, status: str, label: str | None, limit: int
    ) -> list[LinearIssue]:
        if label:
            query = """
        query ReadyIssues($status: String!, $label: String!, $limit: Int!) {
          issues(
            first: $limit
            filter: {
              state: { name: { eq: $status } }
              labels: { name: { eq: $label } }
            }
            orderBy: updatedAt
          ) {
            nodes {
              id
              identifier
              title
              description
              url
              state { name }
              team { key name }
              labels { nodes { name } }
            }
          }
        }
        """
            variables = {"status": status, "label": label, "limit": limit}
        else:
            query = """
        query ReadyIssues($status: String!, $limit: Int!) {
          issues(
            first: $limit
            filter: {
              state: { name: { eq: $status } }
            }
            orderBy: updatedAt
          ) {
            nodes {
              id
              identifier
              title
              description
              url
              state { name }
              team { key name }
              labels { nodes { name } }
            }
          }
        }
        """
            variables = {"status": status, "limit": limit}
        data = await self.graphql(query, variables)
        return [parse_linear_issue(node) for node in data["issues"]["nodes"]]

    async def move_issue(self, issue_id: str, status_name: str) -> None:
        if self._dry_run:
            return
        state_id = await self._state_id(status_name)
        mutation = """
        mutation MoveIssue($issueId: String!, $stateId: String!) {
          issueUpdate(id: $issueId, input: { stateId: $stateId }) {
            success
          }
        }
        """
        await self.graphql(mutation, {"issueId": issue_id, "stateId": state_id})

    async def add_label(self, issue_id: str, label_name: str) -> None:
        if self._dry_run:
            return
        label_id = await self._label_id(label_name)
        label_ids = await self._issue_label_ids(issue_id)
        if label_id not in label_ids:
            label_ids.append(label_id)
        mutation = """
        mutation AddLabel($issueId: String!, $labelIds: [String!]) {
          issueUpdate(id: $issueId, input: { labelIds: $labelIds }) {
            success
          }
        }
        """
        await self.graphql(mutation, {"issueId": issue_id, "labelIds": label_ids})

    async def comment(self, issue_id: str, body: str) -> None:
        if self._dry_run:
            return
        mutation = """
        mutation Comment($issueId: String!, $body: String!) {
          commentCreate(input: { issueId: $issueId, body: $body }) {
            success
          }
        }
        """
        await self.graphql(mutation, {"issueId": issue_id, "body": body})

    async def _state_id(self, status_name: str) -> str:
        query = """
        query WorkflowState($name: String!) {
          workflowStates(first: 1, filter: { name: { eq: $name } }) {
            nodes { id }
          }
        }
        """
        data = await self.graphql(query, {"name": status_name})
        nodes = data["workflowStates"]["nodes"]
        if not nodes:
            raise RuntimeError(f"Linear workflow state not found: {status_name}")
        return nodes[0]["id"]

    async def _label_id(self, label_name: str) -> str:
        query = """
        query IssueLabel($name: String!) {
          issueLabels(first: 1, filter: { name: { eq: $name } }) {
            nodes { id }
          }
        }
        """
        data = await self.graphql(query, {"name": label_name})
        nodes = data["issueLabels"]["nodes"]
        if not nodes:
            raise RuntimeError(f"Linear label not found: {label_name}")
        return nodes[0]["id"]

    async def _issue_label_ids(self, issue_id: str) -> list[str]:
        query = """
        query IssueLabels($issueId: String!) {
          issue(id: $issueId) {
            labels { nodes { id } }
          }
        }
        """
        data = await self.graphql(query, {"issueId": issue_id})
        return [node["id"] for node in data["issue"]["labels"]["nodes"]]
