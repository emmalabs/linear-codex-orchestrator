export type IssueStatus = {
  identifier?: string;
  title?: string;
  url?: string;
  project?: string;
  project_url?: string;
  team?: string;
  workspace_path?: string;
  repos?: Array<{ key: string; github?: string; path?: string; base?: string }> | string;
  changed_repos?: string;
  prs?: string;
  status?: string;
  updated_at?: string;
  archived?: boolean;
  archived_at?: string;
};

export type PullRequestStatus = {
  key?: string;
  title?: string;
  url?: string;
  repo?: string;
  repo_key?: string;
  repo_path?: string;
  branch?: string;
  base?: string;
  issue?: string;
  feedback_count?: number;
  status?: string;
  updated_at?: string;
  archived?: boolean;
  archived_at?: string;
};

export type StageLogSummary = {
  status?: string;
  headline?: string;
  message?: string;
  last_line?: string;
  tokens_used?: number | null;
  file_count?: number;
  files?: Array<{
    path: string;
    added: number;
    removed: number;
  }>;
};

export type StageLog = {
  name: string;
  size: number;
  modified: number;
  summary?: StageLogSummary | null;
};

export type TaskLog = {
  key: string;
  title: string;
  type: string;
  headline?: string;
  modified: number;
  log_count: number;
  file_count: number;
  tokens_used: number;
  stages: StageLog[];
};

export type DashboardData = {
  issues: IssueStatus[];
  prs: PullRequestStatus[];
  archivedIssues: IssueStatus[];
  archivedPrs: PullRequestStatus[];
  tasks: TaskLog[];
  orchestration: string;
  connected: boolean;
  refreshedAt: Date;
};

export type SelectedDetail =
  | { kind: "issue"; item: IssueStatus }
  | { kind: "pr"; item: PullRequestStatus };

export type OrchestratorConfig = {
  workspace_map?: WorkspaceMap;
  auth_mode?: string;
  ready_label?: string;
  running_label?: string;
  blocked_label?: string;
  todo_status?: string;
  in_progress_status?: string;
  in_review_status?: string;
  max_issues_per_tick?: number;
  lock_dir?: string;
  dry_run?: boolean;
  test_command?: string;
  codex_model?: string;
  codex_reasoning_effort?: string;
  codex_fast_mode?: boolean;
  codex_sandbox?: string;
  pr_feedback_branch_prefix?: string;
  linear_api_key?: string;
  hot_reload_config?: boolean;
};

export type ConfigResponse = {
  path: string;
  exists: boolean;
  source?: string;
  config: OrchestratorConfig;
};

export type WorkspaceMap = Record<string, {
  path: string;
  repos: Record<string, {
    github: string;
    path: string;
    base?: string;
  }>;
}>;

export type WorkspaceDraft = {
  id: string;
  teamKey: string;
  path: string;
  repos: RepoDraft[];
};

export type RepoDraft = {
  id: string;
  key: string;
  github: string;
  path: string;
  base: string;
  branches?: string[];
};

export type FolderPickerTarget = {
  title: string;
  path: string;
  requireRepository?: boolean;
  onSelect: (path: string, browse?: BrowseResponse) => void;
};

export type BrowseResponse = {
  path: string;
  parent?: string | null;
  directories: Array<{ name: string; path: string }>;
  current_repository?: BrowseRepository | null;
  repositories?: BrowseRepository[];
};

export type BrowseRepository = {
  key: string;
  github?: string | null;
  path: string;
  base?: string | null;
  branches?: string[];
};

export const emptyData: DashboardData = {
  issues: [],
  prs: [],
  archivedIssues: [],
  archivedPrs: [],
  tasks: [],
  orchestration: "",
  connected: false,
  refreshedAt: new Date()
};
