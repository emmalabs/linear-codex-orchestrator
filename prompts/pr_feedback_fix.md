Address new GitHub PR feedback for `{repo_github}` PR #{pr_number}.

Repository: `{repo_key}`
Local path: `{repo_path}`
PR: `{pr_title}`
URL: `{pr_url}`
Head branch: `{head_branch}`
Base branch: `{base_branch}`

New PR feedback:

{feedback}

Instructions:

- Inspect the current branch and relevant repository code before editing.
- Address only the new PR feedback above and any directly required follow-up changes.
- Preserve the original implementation direction unless the PR feedback identifies it as incorrect.
- Prefer the repository's existing `AGENTS.md` guidance for testing, formatting, comments, and commit conventions.
- Run focused tests or checks when they are available and relevant.
- Do not commit, push, create pull requests, or comment on GitHub.
- Keep the final answer concise.

Final response:

- Summarize which PR feedback items were addressed.
- List files changed.
- List validation commands and results.
- If no code changes were needed, explain why.
