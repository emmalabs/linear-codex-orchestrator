from __future__ import annotations

import asyncio
from pathlib import Path

from .codex_cli import (
    ISSUES_SCHEMA,
    LINEAR_COMMENTS_SCHEMA,
    MUTATION_SCHEMA,
    TEAMS_SCHEMA,
    parse_json_object,
    run_codex,
)
from .models import (
    LinearCommentFeedback,
    LinearIssue,
    LinearTeam,
    linear_comment_feedback_key,
    mark_linear_orchestrator_comment,
)


class LocalLinearClient:
    mutation_retry_delays = (2.0, 5.0, 10.0)

    def __init__(
        self,
        cwd: Path,
        *,
        dry_run: bool = False,
        model: str | None = None,
        reasoning_effort: str | None = None,
        fast_mode: bool = False,
    ) -> None:
        self._cwd = cwd
        self._dry_run = dry_run
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._fast_mode = fast_mode

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
        filters = [f'status exactly "{status}"']
        if label:
            filters.append(f'label exactly "{label}"')
        for excluded_label in exclude_labels:
            filters.append(f'not labeled exactly "{excluded_label}"')
        if team_keys:
            filters.append("team key in " + ", ".join(f'"{team_key}"' for team_key in team_keys))
        filter_text = " and ".join(filters)
        prompt = f"""
Use the configured Linear MCP tools.

Find up to {limit} Linear issues with {filter_text}.
Do not read local files, skills, or repository code. Use only Linear MCP tools.
Return only JSON matching the provided schema. For each issue include:
id, identifier, title, description, url, team_key, team_name, state_name, labels, project_name, project_url.
Use empty strings for project_name and project_url when the issue is not in a Linear project or the project is not visible.
If there are no matching issues, return {{"issues":[]}}.
""".strip()
        raw = run_codex(
            prompt,
            self._cwd,
            model=self._model,
            reasoning_effort=self._reasoning_effort,
            fast_mode=self._fast_mode,
            sandbox="read-only",
            output_schema=ISSUES_SCHEMA,
            timeout_seconds=900,
            show_output=False,
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
                project_name=item.get("project_name") or "",
                project_url=item.get("project_url") or "",
            )
            for item in payload["issues"]
        ]

    async def teams(self, timeout_seconds: int = 20) -> list[LinearTeam]:
        prompt = """
Use the configured Linear MCP tools.

List the visible Linear teams only.
Do not read local files, skills, or repository code. Use only Linear MCP tools.
Return only JSON matching the provided schema. For each team include:
id, key, name.
If there are no visible teams, return {"teams":[]}.
""".strip()
        raw = run_codex(
            prompt,
            self._cwd,
            model=self._model,
            reasoning_effort=self._reasoning_effort,
            fast_mode=self._fast_mode,
            sandbox="read-only",
            output_schema=TEAMS_SCHEMA,
            timeout_seconds=timeout_seconds,
            show_output=False,
        )
        payload = parse_json_object(raw)
        return [
            LinearTeam(
                id=item["id"],
                key=str(item["key"]).upper(),
                name=item["name"],
            )
            for item in payload["teams"]
        ]

    async def issue_context(self, issue: LinearIssue) -> str:
        prompt = f"""
Use the configured Linear MCP tools.

Read Linear issue id "{issue.id}" ({issue.identifier}) and return a complete Markdown context bundle for implementation.
Do not read local files, skills, or repository code. Use only Linear MCP tools.

Include:
- Issue identifier, title, URL, team, state, and labels.
- Full issue description exactly as available, preserving code blocks, JSON snippets, field paths, and punctuation.
- Comments in chronological order, preserving code blocks and important details.
- Attachment names, URLs, and any accessible attachment text/content. If an attachment cannot be read, say so explicitly, but keep any visible issue text that references it.
- Linked PRs/resources if visible.

Do not summarize JSON snippets or dotted paths. Preserve them exactly.
""".strip()
        return run_codex(
            prompt,
            self._cwd,
            model=self._model,
            reasoning_effort=self._reasoning_effort,
            fast_mode=self._fast_mode,
            sandbox="read-only",
            timeout_seconds=900,
            show_output=False,
        )

    async def issue_comments(self, issue: LinearIssue) -> list[LinearCommentFeedback]:
        prompt = f"""
Use the configured Linear MCP tools.

Read comments for Linear issue id "{issue.id}" ({issue.identifier}) only.
Do not mutate Linear. Do not read local files, skills, or repository code. Use only Linear MCP tools.
Return only JSON matching the provided schema. For each comment include:
id, author, body, url, created_at, updated_at.
Use an empty string for url when Linear does not expose a comment URL.
If there are no comments, return {{"comments":[]}}.
""".strip()
        raw = run_codex(
            prompt,
            self._cwd,
            model=self._model,
            reasoning_effort=self._reasoning_effort,
            fast_mode=self._fast_mode,
            sandbox="read-only",
            output_schema=LINEAR_COMMENTS_SCHEMA,
            timeout_seconds=900,
            show_output=False,
        )
        payload = parse_json_object(raw)
        return [
            LinearCommentFeedback(
                key=linear_comment_feedback_key(item["id"], item.get("updated_at") or item.get("created_at") or ""),
                id=item["id"],
                author=item.get("author") or "unknown",
                body=item.get("body") or "",
                url=item.get("url") or f"{issue.url}#comment-{item['id']}",
                created_at=item.get("created_at") or "",
                updated_at=item.get("updated_at") or item.get("created_at") or "",
            )
            for item in payload["comments"]
        ]

    async def move_issue(self, issue_id: str, status_name: str) -> None:
        await self._mutate(
            f'Move Linear issue id "{issue_id}" to status exactly "{status_name}".'
        )

    async def add_label(self, issue_id: str, label_name: str) -> None:
        await self._mutate(
            f'Add Linear label exactly "{label_name}" to issue id "{issue_id}" if it is not already present. '
            "Create the label first if Linear requires it."
        )

    async def remove_label(self, issue_id: str, label_name: str) -> None:
        await self._mutate(
            f'Remove Linear label exactly "{label_name}" from issue id "{issue_id}" if it is present. '
            "If the label is already absent, treat the mutation as successful."
        )

    async def comment(self, issue_id: str, body: str) -> None:
        await self._mutate(
            f'Post this comment on Linear issue id "{issue_id}":\n\n{mark_linear_orchestrator_comment(body)}'
        )

    async def attach_pr(self, issue_id: str, pr_url: str) -> None:
        await self._mutate(
            "Attach this pull request URL to the Linear issue if Linear supports PR attachments; "
            f'otherwise add a concise comment with the URL. Issue id "{issue_id}", PR URL "{pr_url}".'
        )

    async def _mutate(self, instruction: str) -> None:
        if self._dry_run:
            print(f"[dry-run] Linear MCP: {instruction}")
            return
        prompt = f"""
Use the configured Linear MCP tools. Complete this Linear mutation:
Do not read local files, skills, or repository code. Use only Linear MCP tools.

{instruction}

Return only JSON matching the provided schema:
- success: true only if the Linear mutation was actually completed
- message: concise result, including the blocker if success is false
""".strip()
        last_error: Exception | None = None
        attempts = len(self.mutation_retry_delays) + 1
        for attempt in range(1, attempts + 1):
            try:
                raw = run_codex(
                    prompt,
                    self._cwd,
                    model=self._model,
                    reasoning_effort=self._reasoning_effort,
                    fast_mode=self._fast_mode,
                    sandbox="read-only",
                    output_schema=MUTATION_SCHEMA,
                    timeout_seconds=900,
                    bypass_approvals=True,
                    show_output=False,
                )
                payload = parse_json_object(raw)
                if payload.get("success"):
                    return
                message = payload.get("message") or raw
                error = RuntimeError(f"Linear mutation failed: {message}")
            except Exception as exc:
                error = exc
            last_error = error
            if attempt == attempts or not is_transient_linear_error(error):
                raise error
            delay = self.mutation_retry_delays[attempt - 1]
            print(f"Linear mutation transient failure; retrying in {delay:g}s: {error}", flush=True)
            await asyncio.sleep(delay)
        if last_error:
            raise last_error


def is_transient_linear_error(error: Exception) -> bool:
    text = str(error).lower()
    transient_tokens = (
        "auth required",
        "not authenticated",
        "not logged in",
        "cancelled",
        "canceled",
        "timeout",
        "timed out",
        "temporarily",
        "temporary",
        "transient",
        "rate limit",
        "rate-limit",
        "network",
        "connection",
        "econnreset",
        "socket",
        "mcp",
        "server error",
        "internal error",
        "unavailable",
    )
    semantic_tokens = (
        "issue not found",
        "label not found",
        "status not found",
        "state not found",
        "does not exist",
        "unknown status",
        "unknown label",
        "invalid issue",
        "validation",
    )
    return any(token in text for token in transient_tokens) and not any(
        token in text for token in semantic_tokens
    )
