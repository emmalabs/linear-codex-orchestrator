Fix the reviewer findings for this Linear issue in the workspace at `{workspace_path}`.

Issue: `{issue_identifier}` - {issue_title}
URL: {issue_url}

Before fixing reviewer findings, use the configured Linear MCP tools to read
issue `{issue_identifier}` directly. Treat the current Linear issue description,
comments, attachments, and resources as authoritative. The context below is a
fallback snapshot, not a replacement for reading Linear directly.

Full Linear issue context:

{issue_context}

Acceptance scope:

{plan}

Changed repositories:

{changed_repos}

Reviewer findings:

{review_summary}

Requirements:
- Treat the full Linear issue context above as authoritative. Preserve exact JSON paths, keys, nesting, and values from Linear.
- Address only the reviewer findings and any directly required follow-up changes.
- Preserve the existing implementation direction unless the reviewer found it incorrect.
- Inspect the relevant repository code before editing; do not assume the current directory is the only target.
- Prefer each repository's existing `AGENTS.md` guidance for testing, formatting, comments, and commit conventions.
- Use non-interactive commands only.
- Run the most relevant validation available for the changed repositories and report any command failures in the final response.
- Do not push or create pull requests.
- Do not move or comment on Linear issues.
- Leave each repo with only intentional changes.

Final response:
- Summarize which reviewer findings were addressed.
- List the repositories and important files touched.
- List validation commands run and their outcomes.
- Note any remaining assumptions, follow-ups, or blockers that should be preserved on the Linear issue.
