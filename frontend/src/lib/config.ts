import type { OrchestratorConfig, RepoDraft, WorkspaceDraft, WorkspaceMap } from "../types";

export function defaultConfig(): OrchestratorConfig {
  return {
    workspace_map: {},
    auth_mode: "local",
    ready_label: "",
    running_label: "agent-running",
    blocked_label: "agent-blocked",
    todo_status: "Todo",
    in_progress_status: "In Progress",
    in_review_status: "In Review",
    max_issues_per_tick: 1,
    lock_dir: ".locks",
    dry_run: false,
    test_command: "",
    codex_model: "",
    codex_reasoning_effort: "",
    codex_fast_mode: false,
    codex_sandbox: "workspace-write",
    pr_feedback_branch_prefix: "codex/",
    linear_api_key: "",
    hot_reload_config: true
  };
}

export function withConfigDefaults(config: OrchestratorConfig): OrchestratorConfig {
  return { ...defaultConfig(), ...config };
}

export function workspacesFromMap(map: WorkspaceMap): WorkspaceDraft[] {
  return Object.entries(map).map(([teamKey, workspace]) => ({
    id: crypto.randomUUID(),
    teamKey,
    path: workspace.path ?? "",
    repos: Object.entries(workspace.repos ?? {}).map(([key, repo]) => ({
      id: crypto.randomUUID(),
      key,
      github: repo.github ?? "",
      path: repo.path ?? "",
      base: repo.base ?? "main",
      branches: repo.base ? [repo.base] : ["main"]
    }))
  }));
}

export function workspaceMapFromDraft(workspaces: WorkspaceDraft[]): WorkspaceMap {
  return workspaces.reduce<WorkspaceMap>((map, workspace) => {
    const teamKey = workspace.teamKey.trim().toUpperCase();
    if (!teamKey) {
      return map;
    }
    map[teamKey] = {
      path: workspace.path.trim(),
      repos: workspace.repos.reduce<WorkspaceMap[string]["repos"]>((repos, repo) => {
        const key = repo.key.trim();
        if (!key) {
          return repos;
        }
        repos[key] = {
          github: repo.github.trim(),
          path: repo.path.trim(),
          base: repo.base.trim() || "main"
        };
        return repos;
      }, {})
    };
    return map;
  }, {});
}

export function cleanConfig(config: OrchestratorConfig): OrchestratorConfig {
  return Object.fromEntries(
    Object.entries(config).map(([key, value]) => [
      key,
      typeof value === "string" ? value.trim() : value
    ])
  ) as OrchestratorConfig;
}

export function emptyWorkspace(): WorkspaceDraft {
  return {
    id: crypto.randomUUID(),
    teamKey: "",
    path: "",
    repos: [emptyRepo()]
  };
}

export function emptyRepo(): RepoDraft {
  return {
    id: crypto.randomUUID(),
    key: "",
    github: "",
    path: "",
    base: "main",
    branches: []
  };
}
