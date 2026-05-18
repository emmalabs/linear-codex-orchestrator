# emma-linear-codex-orchestrator

Small local service that polls Linear Todo issues and coordinates your existing command-line tools:

1. Codex CLI for planning, implementation, and review.
2. Linear GraphQL API for fast polling, status, labels, and comments when `LINEAR_API_KEY` is set.
   If no API key is present, Linear MCP through Codex CLI is used as a fallback.
3. GitHub CLI for ready-for-review PR creation/update and PR feedback checks.

By default, every Todo issue is eligible. Set `LINEAR_READY_LABEL` if you later want an explicit label gate such as `codex-ready`.

## Prerequisites

No API keys are required in `.env`; without `LINEAR_API_KEY`, the daemon uses your authenticated Linear MCP. `./scripts/setup.sh` installs missing command-line prerequisites when it can:

- Python 3.9+
- Node.js/npm for the React dashboard build
- Git
- GitHub CLI (`gh`)
- Codex CLI (`codex`)

If `codex` is not already installed, setup installs it globally with `npm install -g @openai/codex`. Authentication is still interactive:

- Run `codex --login` if setup reports Codex is not logged in.
- Run `codex mcp login linear` if `codex mcp list` shows Linear as `Not logged in`.
- Confirm `codex mcp list` shows Linear enabled and authenticated.
- For faster Linear polling and comments, set `LINEAR_API_KEY` to a Linear personal API key.
- Run `gh auth login` if setup reports GitHub CLI is not authenticated.
- Confirm `gh` has access to `emmalabs`.
- Leave `CODEX_MODEL` empty unless you know a specific model works with your Codex account.
- Set `CODEX_REASONING_EFFORT` to `low`, `medium`, `high`, or `xhigh` for models that support it.
- Set `CODEX_FAST_MODE=true` to request the Codex Fast service tier for supported models.
- The daemon console shows orchestration progress only. Detailed Codex stage output is written under `.logs/`.

## Quick Start

```bash
cd emma-linear-codex-orchestrator
./scripts/setup.sh
```

Check `.env`, then run one polling tick:

```bash
DRY_RUN=true ./scripts/run.sh once
```

Run continuously with a 1-minute interval:

```bash
DRY_RUN=false ./scripts/run.sh
```

Daemon mode also starts the React dashboard at `http://127.0.0.1:8765` so you can follow orchestration logs, issue/PR status, and inspect detailed Codex stage logs. `./scripts/setup.sh` builds the dashboard, and `./scripts/run.sh` builds it automatically if `frontend/dist/` is missing.

Each normal tick first checks open `codex/` PRs for new GitHub comments and review feedback, then polls Linear for new implementation work.

Run only the PR feedback worker:

```bash
./scripts/run.sh pr-comments-once
./scripts/run.sh pr-comments-daemon
```

Or schedule `./scripts/run.sh once` from cron or systemd on this machine.

## Required Config

`WORKSPACE_MAP_JSON` maps the single Linear team to the local multi-repo workspace:

```json
{
  "EMMA": {
    "path": "/home/aleix/Projects/emma.db",
    "repos": {
      "api": {
        "github": "emmalabs/emma.db-api",
        "path": "/home/aleix/Projects/emma.db/emma-api",
        "base": "develop"
      }
    }
  }
}
```

For the `emma.db` workspace, Codex can change any of the configured repos: `api`, `app`, `data`, and `docker`. No Linear repo labels are required.

## Prompts

Codex prompts live in Markdown files under `prompts/`:

- `planner.md`
- `implementation.md`
- `optimizer.md`
- `reviewer.md`
- `review_fix.md`
- `pr_feedback_fix.md`

Edit those files to tune behavior without changing Python code.

## Dashboard

The web UI is a Vite React app under `frontend/`. The Python daemon serves the compiled static files from `frontend/dist/` and exposes these local APIs:

- `/api/status`
- `/api/orchestrator`
- `/api/logs`
- `/logs/<name>`

For frontend-only development, run:

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

The Vite dev server proxies API calls to the daemon on `127.0.0.1:8765`.

## Safety Model

- Todo issues are processed by default; `LINEAR_READY_LABEL` can optionally narrow the queue.
- A local lock file prevents concurrent work per workspace.
- Each issue gets the same branch name in every candidate repo: `codex/<ISSUE-ID>-<slug>`.
- The service never pushes to `main`.
- The reviewer runs Codex in read-only sandbox mode.
- `DRY_RUN=true` avoids mutating GitHub/Linear and skips pushing.
- Human merge approval remains outside this service.
