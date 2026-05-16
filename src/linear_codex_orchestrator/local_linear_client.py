from __future__ import annotations

from pathlib import Path

from .codex_cli import ISSUES_SCHEMA, parse_json_object, run_codex
from .models import LinearIssue


class LocalLinearClient:
    def __init__(
        self,
        cwd: Path,
        *,
        dry_run: bool = False,
        model: str | None = None,
    ) -> None:
        self._cwd = cwd
        self._dry_run = dry_run
        self._model = model

    async def close(self) -> None:
        return None

    async def ready_issues(
        self, status: str, label: str | None, limit: int
    ) -> list[LinearIssue]:
        filters = [f'status exactly "{status}"']
        if label:
            filters.append(f'label exactly "{label}"')
        filter_text = " and ".join(filters)
        prompt = f"""
Use the configured Linear MCP tools.

Find up to {limit} Linear issues with {filter_text}.
Return only JSON matching the provided schema. For each issue include:
id, identifier, title, description, url, team_key, team_name, state_name, labels.
If there are no matching issues, return {{"issues":[]}}.
""".strip()
        raw = run_codex(
            prompt,
            self._cwd,
            model=self._model,
            sandbox="read-only",
            output_schema=ISSUES_SCHEMA,
            timeout_seconds=900,
        )
        payload = parse_json_object(raw)
        return [
            LinearIssue(
                id=item["id"],
                identifier=item["identifier"],
                title=item["title"],
                description=item.get("description") or "",
                url=item["url"],
                team_key=item["team_key"],
                team_name=item["team_name"],
                state_name=item["state_name"],
                labels=tuple(item.get("labels") or []),
            )
            for item in payload["issues"]
        ]

    async def move_issue(self, issue_id: str, status_name: str) -> None:
        await self._mutate(
            f'Move Linear issue id "{issue_id}" to status exactly "{status_name}".'
        )

    async def add_label(self, issue_id: str, label_name: str) -> None:
        await self._mutate(
            f'Add Linear label exactly "{label_name}" to issue id "{issue_id}" if it is not already present.'
        )

    async def comment(self, issue_id: str, body: str) -> None:
        await self._mutate(
            f'Post this comment on Linear issue id "{issue_id}":\n\n{body}'
        )

    async def _mutate(self, instruction: str) -> None:
        if self._dry_run:
            print(f"[dry-run] Linear MCP: {instruction}")
            return
        prompt = f"""
Use the configured Linear MCP tools. Complete this Linear mutation:

{instruction}

After the mutation, respond with one concise sentence.
""".strip()
        run_codex(prompt, self._cwd, model=self._model, sandbox="read-only", timeout_seconds=900)
