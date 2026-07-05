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

1. Before making any code changes, inspect the current state of the GitHub Pull Request.
   - Review the new PR review comments provided above.
   - Check for any unresolved review threads.
   - Check the status of all GitHub Actions / CI checks for the current HEAD commit.
   - If any required workflow has failed, inspect the workflow logs to determine the root cause.
   - Distinguish between failures caused by this branch and unrelated or flaky failures.

2. Inspect the current branch and the relevant repository code before editing.

3. Address:
   - The new PR feedback above.
   - Any failing GitHub Actions that are caused by the current branch and can reasonably be fixed as part of this task.
   - Any directly required follow up changes.

4. Preserve the original implementation direction unless the PR feedback identifies it as incorrect.

5. Prefer the repository's existing `AGENTS.md` guidance for testing, formatting, comments, and commit conventions.

6. Run focused tests or checks when they are available and relevant.

7. If GitHub Actions remain failing after your changes:
   - Explain which workflows are still failing.
   - Explain whether they are unrelated to your changes or require additional work.

8. Do not commit, push, create pull requests, approve reviews, dismiss review comments, or comment on GitHub.

9. Keep the final answer concise.

Final response:

- Summary of PR feedback addressed.
- GitHub Actions status:
  - Initial status.
  - Workflows that were failing.
  - Which failures were fixed.
  - Remaining failures (if any) and why.
- Files changed.
- Validation commands executed and their results.
- If no code changes were needed, explain why.