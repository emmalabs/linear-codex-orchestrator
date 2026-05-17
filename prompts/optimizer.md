Clean up and improve the implementation for this Linear issue in the workspace at `{workspace_path}`.

Issue: `{issue_identifier}` — {issue_title}
URL: {issue_url}

Before editing, use the configured Linear MCP tools to read issue `{issue_identifier}` directly. Treat the current Linear issue description, comments, attachments, and linked resources as authoritative. The context below is only a fallback snapshot in case Linear cannot be fully read; do not rely on it instead of reading Linear directly.

Fallback Linear issue context:

{issue_context}

Acceptance scope:

{plan}

Changed repositories:

{changed_repos}

Implementation summary:

{implementation_summary}

Requirements:
* Preserve the implemented behavior and acceptance scope exactly.
* Do not add new behavior beyond the accepted implementation.
* Preserve exact JSON paths, keys, nesting, and values from the Linear issue.
* Focus only on low-risk maintainability, clean architecture alignment, and directly related performance improvements.
* Apply best software engineering practices where they fit the existing codebase: clear responsibilities, simple control flow, readable names, minimal duplication, good error handling, and tests that cover meaningful behavior.
* Preserve clean architecture boundaries already present in the repository. Keep business logic, infrastructure, transport, persistence, and presentation concerns separated according to the project’s existing patterns.
* Review performance implications of the implemented changes, especially on hot paths, database queries, loops, serialization, network calls, and payload size.
* Apply only low-risk performance improvements that are directly related to the implementation and do not change behavior or broaden scope.
* Do not introduce caching, batching, concurrency, schema changes, dependency changes, or architectural changes unless they are already part of the accepted implementation scope.
* If a potential performance issue requires a broader change, do not implement it; note it as a follow-up in the final response.
* Review every new function, method, class, helper, and abstraction introduced by the implementation.
* Keep it only if it has a clear reason to exist: it reduces duplication, improves readability, isolates a meaningful responsibility, protects an architectural boundary, or makes behavior easier to test.
* Inline or remove new helpers that merely wrap one or two obvious operations, obscure control flow, duplicate existing repository patterns, or are not reused and do not clarify intent.
* Prefer existing project conventions and nearby patterns over introducing new abstractions.
* Do not perform broad refactors, cosmetic churn, dependency changes, unrelated performance work, or architectural changes.
* For each changed repository, read and follow its `AGENTS.md` guidance for testing, formatting, comments, and commit conventions before editing.
* Keep tests and validation aligned with the implementation.
* Use non-interactive commands only.
* Run the most relevant validation available for each changed repository and report any command failures in the final response.
* Do not push, create pull requests, move Linear issues, or comment on Linear issues.
* Leave each repository with only intentional changes.
* Clean up any unused code or leftover changes from previous steps.

Final response:
* Summarize optimization and cleanup changes made, or say explicitly if no cleanup was needed.
* List the repositories and important files touched.
* List validation commands run and their outcomes.
* Note any assumptions, follow-ups, or blockers that should be preserved on the Linear issue.