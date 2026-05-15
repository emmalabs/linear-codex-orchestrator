# emma-linear-codex-orchestrator

Small Python service that polls Linear Todo issues and uses the OpenAI Agents SDK to coordinate:

1. A planner agent that scopes the Linear issue.
2. A Codex-backed implementer agent connected through `codex mcp-server`.
3. A reviewer agent that can inspect status, diff, and test output but cannot edit.
4. GitHub PR creation/update.
5. Linear comments and status transitions.

By default, every Todo issue is eligible. Set `LINEAR_READY_LABEL` if you later want an explicit label gate such as `codex-ready`.

## Quick Start

```bash
cd emma-linear-codex-orchestrator
./scripts/setup.sh
```

Fill in `.env`, then run one polling tick:

```bash
. .venv/bin/activate
DRY_RUN=true emma-linear-codex-orchestrator once
```

Run continuously with a 15-minute interval:

```bash
emma-linear-codex-orchestrator daemon
```

Or schedule `emma-linear-codex-orchestrator once` from cron, GitHub Actions, Cloud Run, or another scheduler.

## Required Config

`REPO_MAP_JSON` maps Linear issues to checked-out repositories:

```json
{
  "api": {
    "github": "emmalabs/emma.db-api",
    "path": "/home/aleix/Projects/emma.db/emma-api",
    "base": "develop",
    "label": "repo:api"
  }
}
```

For the `emma.db` workspace, route Linear issues with one of these labels:

- `repo:api`
- `repo:app`
- `repo:data`
- `repo:docker`

If no repository label is present, the key can still fall back to the Linear team key.

## Safety Model

- Todo issues are processed by default; `LINEAR_READY_LABEL` can optionally narrow the queue.
- A local lock file prevents concurrent work per repository.
- Each issue gets its own branch: `codex/<ISSUE-ID>-<slug>`.
- The service never pushes to `main`.
- The reviewer agent only receives read-only tools.
- `DRY_RUN=true` avoids mutating GitHub/Linear and skips pushing.
- Human merge approval remains outside this service.

## GitHub Actions Schedule

Copy `.github/workflows/orchestrator.yml` into the deployment repo and configure secrets:

- `OPENAI_API_KEY`
- `LINEAR_API_KEY`
- `GH_PAT`
- `REPO_MAP_JSON`

For real deployments, prefer a runner that has the target repositories already checked out at stable paths.
