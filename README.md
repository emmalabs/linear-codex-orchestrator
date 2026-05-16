# emma-linear-codex-orchestrator

Small local service that polls Linear Todo issues and coordinates your existing command-line tools:

1. Codex CLI for planning, implementation, and review.
2. Linear MCP, through Codex CLI, for polling issues, moving status, labels, and comments.
3. GitHub CLI for draft PR creation/update.

By default, every Todo issue is eligible. Set `LINEAR_READY_LABEL` if you later want an explicit label gate such as `codex-ready`.

## Prerequisites

No API keys are required in `.env`.

- `codex` is installed and logged in.
- `codex mcp list` shows Linear enabled and authenticated.
- `gh` is installed and authenticated with access to `emmalabs`.

## Quick Start

```bash
cd emma-linear-codex-orchestrator
./scripts/setup.sh
```

Check `.env`, then run one polling tick:

```bash
DRY_RUN=true ./scripts/run.sh once
```

Run continuously with a 15-minute interval:

```bash
DRY_RUN=false ./scripts/run.sh daemon
```

Or schedule `./scripts/run.sh once` from cron or systemd on this WSL machine.

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
- The reviewer runs Codex in read-only sandbox mode.
- `DRY_RUN=true` avoids mutating GitHub/Linear and skips pushing.
- Human merge approval remains outside this service.
