Address new Linear issue feedback for `{issue_identifier}` in the workspace at `{workspace_path}`.

Before fixing the feedback, use the configured Linear MCP tools to read issue
`{issue_identifier}` directly. Treat the current Linear issue description,
comments, attachments, and resources as authoritative. The context below is a
fallback snapshot, not a replacement for reading Linear directly.

Full Linear issue context:

{issue_context}

Existing branch: `{branch}`

Candidate repositories:
{repositories}

New Linear feedback:

{feedback}

Requirements:
- Address only the new Linear feedback above and any directly required follow-up changes.
- Preserve the original implementation direction unless the Linear feedback identifies it as incorrect.
- Inspect the relevant repository code before editing; do not assume every repository needs changes.
- Prefer each repository's existing `AGENTS.md` guidance for testing, formatting, comments, and commit conventions.
- Add or update tests when the feedback warrants it.
- Run the most relevant validation available for the changed repositories and report any command failures.
- Do not commit, push, create pull requests, move Linear issues, or comment on Linear.
- Leave each repo with only intentional changes.

Final response:
- Summarize which Linear feedback comments were addressed.
- List repositories and important files touched.
- List validation commands run and outcomes.
- Note any assumptions, follow-ups, or blockers that should be preserved on the Linear issue.
