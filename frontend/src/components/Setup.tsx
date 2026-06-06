import * as React from "react";
import {
  IconAdjustments,
  IconAlertTriangle,
  IconBriefcase,
  IconBrandGithub,
  IconExternalLink,
  IconFolder,
  IconGitBranch,
  IconPencil,
  IconPlus,
  IconTrash
} from "@tabler/icons-react";
import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Box,
  Button,
  Group,
  Modal,
  NumberInput,
  Paper,
  PasswordInput,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  ThemeIcon,
  Title,
  UnstyledButton
} from "@mantine/core";
import type {
  BrowseRepository,
  BrowseResponse,
  ConfigResponse,
  FolderPickerTarget,
  OrchestratorConfig,
  RepoDraft,
  WorkspaceDraft
} from "../types";
import {
  cleanConfig,
  defaultConfig,
  emptyRepo,
  emptyWorkspace,
  withConfigDefaults,
  workspaceMapFromDraft,
  workspacesFromMap
} from "../lib/config";

type SetupSection = "workspaces" | "orchestrator";
type SaveStatus = "idle" | "saving" | "saved" | "error";

export function SetupView(props: {
  configResponse: ConfigResponse | null;
  section: SetupSection;
}) {
  const [draft, setDraft] = React.useState<OrchestratorConfig>(() => defaultConfig());
  const [workspaces, setWorkspaces] = React.useState<WorkspaceDraft[]>([]);
  const [saveStatus, setSaveStatus] = React.useState<SaveStatus>("idle");
  const [error, setError] = React.useState<string | null>(null);
  const [pickerTarget, setPickerTarget] = React.useState<FolderPickerTarget | null>(null);
  const hydratedRef = React.useRef(false);
  const lastSavedPayloadRef = React.useRef("");

  React.useEffect(() => {
    const config = withConfigDefaults(props.configResponse?.config ?? {});
    const nextWorkspaces = workspacesFromMap(config.workspace_map ?? {});
    lastSavedPayloadRef.current = JSON.stringify(configPayload(config, nextWorkspaces));
    hydratedRef.current = true;
    setDraft(config);
    setWorkspaces(nextWorkspaces);
    setSaveStatus("idle");
  }, [props.configResponse]);

  const updateDraft = (key: keyof OrchestratorConfig, value: string | number | boolean | undefined) => {
    setDraft((current) => ({ ...current, [key]: value }));
  };

  const currentPayload = React.useMemo(
    () => configPayload(draft, workspaces),
    [draft, workspaces]
  );
  const currentPayloadJson = React.useMemo(() => JSON.stringify(currentPayload), [currentPayload]);

  const savePayload = React.useCallback(async (requestPayload: OrchestratorConfig) => {
    setSaveStatus("saving");
    setError(null);
    try {
      const response = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestPayload)
      });
      const payload = await response.json() as { ok?: boolean; error?: string };
      if (!response.ok) {
        throw new Error(payload.error || "Failed to save config.");
      }
      lastSavedPayloadRef.current = JSON.stringify(requestPayload);
      setSaveStatus("saved");
    } catch (saveError) {
      setSaveStatus("error");
      setError(saveError instanceof Error ? saveError.message : "Failed to save config.");
    }
  }, []);

  React.useEffect(() => {
    if (!hydratedRef.current || currentPayloadJson === lastSavedPayloadRef.current) {
      return;
    }
    setSaveStatus("idle");
    const timer = window.setTimeout(() => {
      void savePayload(currentPayload);
    }, 800);
    return () => window.clearTimeout(timer);
  }, [currentPayload, currentPayloadJson, savePayload]);

  const repoCount = workspaces.reduce((total, workspace) => total + workspace.repos.length, 0);
  const pageTitle = props.section === "workspaces" ? "Workspaces" : "Orchestrator";
  const pageDescription = props.section === "workspaces"
    ? "Map Linear teams to local folders and GitHub repositories."
    : "Configure Linear routing, runtime behavior, and how Codex runs.";

  return (
    <Stack className="setup-stack linear-settings-page" gap="md">
      <SetupToolbar
        configResponse={props.configResponse}
        description={pageDescription}
        hotReload={draft.hot_reload_config !== false}
        icon={props.section === "workspaces" ? <IconBriefcase size={20} /> : <IconAdjustments size={20} />}
        saveStatus={saveStatus}
        title={pageTitle}
        action={props.section === "workspaces" ? (
          <Button
            leftSection={<IconPlus size={14} />}
            onClick={() => setWorkspaces((current) => [...current, emptyWorkspace()])}
            variant="light"
          >
            Add workspace
          </Button>
        ) : null}
      />

      {error ? (
        <Alert color="red" variant="light" icon={<IconAlertTriangle size={18} />} title="Could not save" withCloseButton onClose={() => setError(null)}>
          {error}
        </Alert>
      ) : null}

      {props.section === "workspaces" ? (
        <>
          <Stack className="linear-settings-content" gap="xl">
            {workspaces.length ? (
              <Stack gap="xl">
                {workspaces.map((workspace) => (
                  <WorkspaceEditor
                    key={workspace.id}
                    onBrowse={(target) => setPickerTarget(target)}
                    workspace={workspace}
                    onChange={(next) => setWorkspaces((current) => current.map((item) => item.id === next.id ? next : item))}
                    onRemove={() => setWorkspaces((current) => current.filter((item) => item.id !== workspace.id))}
                  />
                ))}
              </Stack>
            ) : (
              <EmptyState
                icon={<IconBriefcase size={22} />}
                text="No workspaces yet. Add at least one Linear team to start orchestrating."
              />
            )}
          </Stack>
        </>
      ) : (
        <>
          <Stack className="linear-settings-content" gap="xl">
            <SettingsGroup title="Linear routing">
              <SettingRow label="Ready label" description="Only pick up issues with this label when set.">
                <TextInput aria-label="Ready label" value={draft.ready_label ?? ""} onChange={(event) => updateDraft("ready_label", event.currentTarget.value)} placeholder="Optional label gate" />
              </SettingRow>
              <SettingRow label="Running label" description="Applied while Codex is working on an issue.">
                <TextInput aria-label="Running label" value={draft.running_label ?? ""} onChange={(event) => updateDraft("running_label", event.currentTarget.value)} />
              </SettingRow>
              <SettingRow label="Blocked label" description="Applied when automation cannot continue.">
                <TextInput aria-label="Blocked label" value={draft.blocked_label ?? ""} onChange={(event) => updateDraft("blocked_label", event.currentTarget.value)} />
              </SettingRow>
              <SettingRow label="Linear API key" description="Used for Linear API access when configured.">
                <PasswordInput aria-label="Linear API key" value={draft.linear_api_key ?? ""} onChange={(event) => updateDraft("linear_api_key", event.currentTarget.value)} placeholder="Optional" />
              </SettingRow>
              <SettingRow label="Todo status">
                <TextInput aria-label="Todo status" value={draft.todo_status ?? ""} onChange={(event) => updateDraft("todo_status", event.currentTarget.value)} />
              </SettingRow>
              <SettingRow label="In progress status">
                <TextInput aria-label="In progress status" value={draft.in_progress_status ?? ""} onChange={(event) => updateDraft("in_progress_status", event.currentTarget.value)} />
              </SettingRow>
              <SettingRow label="In review status">
                <TextInput aria-label="In review status" value={draft.in_review_status ?? ""} onChange={(event) => updateDraft("in_review_status", event.currentTarget.value)} />
              </SettingRow>
            </SettingsGroup>

            <SettingsGroup title="Runtime">
              <SettingRow label="Max issues per tick" description="Maximum queue items processed each daemon tick.">
                <NumberInput aria-label="Max issues per tick" min={1} value={draft.max_issues_per_tick ?? 1} onChange={(value) => updateDraft("max_issues_per_tick", Number(value) || 1)} />
              </SettingRow>
              <SettingRow label="Lock dir" description="Directory used to coordinate active work.">
                <TextInput aria-label="Lock dir" value={draft.lock_dir ?? ""} onChange={(event) => updateDraft("lock_dir", event.currentTarget.value)} />
              </SettingRow>
              <SettingRow label="Test command" description="Optional command Codex can run before finishing.">
                <TextInput aria-label="Test command" value={draft.test_command ?? ""} onChange={(event) => updateDraft("test_command", event.currentTarget.value)} placeholder="Optional" />
              </SettingRow>
              <SettingRow label="PR branch prefix">
                <TextInput aria-label="PR branch prefix" value={draft.pr_feedback_branch_prefix ?? ""} onChange={(event) => updateDraft("pr_feedback_branch_prefix", event.currentTarget.value)} />
              </SettingRow>
              <SettingRow label="Dry run" description="Plan actions without pushing changes.">
                <Switch aria-label="Dry run" checked={Boolean(draft.dry_run)} onChange={(event) => updateDraft("dry_run", event.currentTarget.checked)} />
              </SettingRow>
              <SettingRow label="Hot reload config" description="Apply saved setup changes before the next daemon tick.">
                <Switch aria-label="Hot reload config" checked={draft.hot_reload_config !== false} onChange={(event) => updateDraft("hot_reload_config", event.currentTarget.checked)} />
              </SettingRow>
            </SettingsGroup>

            <SettingsGroup title="Codex">
              <SettingRow label="Model" description="Leave blank to use the account default.">
                <Select
                  aria-label="Model"
                  clearable
                  data={[{ value: "gpt-5.5", label: "gpt-5.5" }]}
                  onChange={(value) => updateDraft("codex_model", value ?? "")}
                  placeholder="Default account model"
                  value={draft.codex_model || null}
                />
              </SettingRow>
              <SettingRow label="Reasoning effort">
                <Select
                  aria-label="Reasoning effort"
                  clearable
                  data={["low", "medium", "high", "xhigh"]}
                  onChange={(value) => updateDraft("codex_reasoning_effort", value ?? "")}
                  placeholder="Default"
                  value={draft.codex_reasoning_effort || null}
                />
              </SettingRow>
              <SettingRow label="Sandbox" description="Filesystem access Codex gets while it works.">
                <Select
                  aria-label="Sandbox"
                  data={[
                    { value: "read-only", label: "Read only" },
                    { value: "workspace-write", label: "Workspace write" },
                    { value: "danger-full-access", label: "Full access" }
                  ]}
                  onChange={(value) => updateDraft("codex_sandbox", value ?? "workspace-write")}
                  value={draft.codex_sandbox || "workspace-write"}
                />
              </SettingRow>
              <SettingRow label="Fast mode" description="Trade depth for speed.">
                <Switch aria-label="Fast mode" checked={Boolean(draft.codex_fast_mode)} onChange={(event) => updateDraft("codex_fast_mode", event.currentTarget.checked)} />
              </SettingRow>
            </SettingsGroup>
          </Stack>
        </>
      )}

      <FolderPickerModal target={pickerTarget} onClose={() => setPickerTarget(null)} />
    </Stack>
  );
}

function SettingsGroup(props: { title: string; children: React.ReactNode }) {
  return (
    <Box className="settings-group">
      <Title order={3} size="sm" className="settings-group-title">{props.title}</Title>
      <Paper withBorder className="settings-card">
        {props.children}
      </Paper>
    </Box>
  );
}

function configPayload(draft: OrchestratorConfig, workspaces: WorkspaceDraft[]): OrchestratorConfig {
  return cleanConfig({ ...draft, workspace_map: workspaceMapFromDraft(workspaces) });
}

function SettingRow(props: {
  label: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="settings-row">
      <Box miw={0}>
        <Text fw={600} size="sm">{props.label}</Text>
        {props.description ? <Text c="dimmed" size="xs">{props.description}</Text> : null}
      </Box>
      <Box className="settings-row-control">
        {props.children}
      </Box>
    </div>
  );
}

function SetupToolbar(props: {
  action?: React.ReactNode;
  configResponse: ConfigResponse | null;
  description: string;
  hotReload: boolean;
  icon: React.ReactNode;
  saveStatus: SaveStatus;
  title: string;
}) {
  const statusText = props.saveStatus === "saving"
    ? "Autosaving..."
    : props.saveStatus === "saved"
      ? "Saved"
      : props.saveStatus === "error"
        ? "Autosave failed"
        : "";
  const statusColor = props.saveStatus === "error" ? "red" : "dimmed";
  return (
    <Paper withBorder p="md" className="setup-toolbar">
      <Group align="center" justify="space-between" gap="md">
        <Group align="center" gap="sm" miw={0}>
          <ThemeIcon size={38} radius="md" variant="light">
            {props.icon}
          </ThemeIcon>
          <Box miw={0}>
            <Group gap="xs">
              <Title order={2} size="h3">{props.title}</Title>
              <Badge color={props.configResponse?.exists ? "green" : "yellow"} variant="light">
                {props.configResponse?.exists ? "Config found" : "Not saved"}
              </Badge>
            </Group>
            <Text c="dimmed" size="sm">{props.description}</Text>
            <Text c="dimmed" size="xs" className="truncate">
              {props.configResponse?.path ?? "Loading config path..."}
              {props.configResponse?.source ? ` · source: ${props.configResponse.source}` : ""}
              {" · "}
              {props.hotReload ? "applies next tick" : "restart required"}
            </Text>
          </Box>
        </Group>
        <Group gap="sm" justify="flex-end">
          {statusText ? <Text c={statusColor} size="xs">{statusText}</Text> : null}
          {props.action}
        </Group>
      </Group>
    </Paper>
  );
}

function EmptyState(props: { icon: React.ReactNode; text: string }) {
  return (
    <Stack className="setup-empty" align="center" gap="xs" py="lg">
      <ThemeIcon size={42} radius="xl" variant="light" color="gray">
        {props.icon}
      </ThemeIcon>
      <Text c="dimmed" size="sm" ta="center">{props.text}</Text>
    </Stack>
  );
}

function WorkspaceEditor(props: {
  workspace: WorkspaceDraft;
  onChange: (workspace: WorkspaceDraft) => void;
  onBrowse: (target: FolderPickerTarget) => void;
  onRemove: () => void;
}) {
  const update = (patch: Partial<WorkspaceDraft>) => props.onChange({ ...props.workspace, ...patch });
  const selectWorkspacePath = (path: string, browse?: BrowseResponse) => {
    const detectedRepos = browse?.current_repository
      ? [browse.current_repository, ...(browse.repositories ?? [])]
      : browse?.repositories ?? [];
    update({
      path,
      repos: mergeDetectedRepos(props.workspace.repos, detectedRepos),
    });
  };
  const repoCount = props.workspace.repos.length;
  return (
    <Box className="workspace-block">
      <Group className="workspace-block-header" justify="space-between" gap="sm" wrap="nowrap">
        <Box miw={0}>
          <Title order={3} size="sm" className="truncate">{props.workspace.teamKey || "New workspace"}</Title>
          <Text c="dimmed" size="xs" className="truncate">
            {repoCount} repo{repoCount === 1 ? "" : "s"}
            {props.workspace.path ? ` · ${props.workspace.path}` : " · no folder set"}
          </Text>
        </Box>
        <ActionIcon color="red" onClick={props.onRemove} variant="subtle" size="lg" aria-label="Remove workspace">
          <IconTrash size={16} />
        </ActionIcon>
      </Group>
      <Paper withBorder className="settings-card">
        <SettingRow label="Linear team key" description="Linear team key that maps issues to this workspace.">
          <TextInput aria-label="Linear team key" value={props.workspace.teamKey} onChange={(event) => update({ teamKey: event.currentTarget.value.toUpperCase() })} placeholder="e.g. EMMA" />
        </SettingRow>
        <SettingRow label="Workspace folder" description="Parent folder that contains the checked-out repositories.">
          <Group gap="xs" wrap="nowrap">
            <TextInput className="grow-input" aria-label="Workspace path" value={props.workspace.path} onChange={(event) => update({ path: event.currentTarget.value })} placeholder="Browse to detect repos" />
            <ActionIcon
              onClick={() => props.onBrowse({
                title: "Select workspace folder",
                path: props.workspace.path,
                onSelect: selectWorkspacePath
              })}
              variant="light"
              size="lg"
              aria-label="Browse workspace folder"
            >
              <IconFolder size={16} />
            </ActionIcon>
          </Group>
        </SettingRow>
        <div className="settings-row workspace-repositories-heading">
          <Box>
            <Text fw={600} size="sm">Repositories</Text>
            <Text c="dimmed" size="xs">Checked-out repositories Codex can work in.</Text>
          </Box>
          <Group justify="flex-end" gap="xs">
            <Button
              leftSection={<IconPlus size={14} />}
              onClick={() => update({ repos: [...props.workspace.repos, emptyRepo()] })}
              size="compact-xs"
              variant="subtle"
            >
              Repo
            </Button>
          </Group>
        </div>
        {props.workspace.repos.length ? (
          props.workspace.repos.map((repo) => (
            <RepoEditor
              key={repo.id}
              onBrowse={(target) => props.onBrowse(target)}
              repo={repo}
              onChange={(next) => update({ repos: props.workspace.repos.map((item) => item.id === next.id ? next : item) })}
              onRemove={() => update({ repos: props.workspace.repos.filter((item) => item.id !== repo.id) })}
            />
          ))
        ) : (
          <div className="settings-row">
            <Text c="dimmed" size="sm">No repositories yet. Add one or browse the workspace folder to detect them.</Text>
          </div>
        )}
      </Paper>
    </Box>
  );
}

function RepoEditor(props: {
  repo: RepoDraft;
  onBrowse: (target: FolderPickerTarget) => void;
  onChange: (repo: RepoDraft) => void;
  onRemove: () => void;
}) {
  const update = (patch: Partial<RepoDraft>) => props.onChange({ ...props.repo, ...patch });
  const pathLocked = Boolean(props.repo.path.trim());
  const branchOptions = branchSelectOptions(props.repo);
  const githubUrl = githubRepoUrl(props.repo.github);
  const updateRepoPath = (path: string, browse?: BrowseResponse) => {
    const detected = browse?.current_repository;
    if (!detected) {
      return;
    }
    const base = detected.base || "main";
    update({
      path,
      key: detected.key || repoKeyFromPath(path),
      github: detected.github || "",
      base,
      branches: detected.branches?.length ? detected.branches : [base]
    });
  };
  const browseFolder = () => props.onBrowse({
    title: "Select repository folder",
    path: props.repo.path,
    requireRepository: true,
    onSelect: updateRepoPath
  });
  return (
    <div className="settings-row repo-settings-row">
      <Box className="repo-main" miw={0}>
        <Group className="repo-title-line" gap={6} mb={3} miw={0} wrap="nowrap">
          <IconBrandGithub className="repo-host-icon" size={15} />
          {githubUrl ? (
            <Anchor className="truncate" fw={600} href={githubUrl} rel="noopener noreferrer" size="sm" target="_blank">
              {props.repo.github}
            </Anchor>
          ) : (
            <Text className="truncate" fw={600} size="sm">{props.repo.key || "Repository"}</Text>
          )}
          {githubUrl ? <IconExternalLink className="repo-external-icon" size={13} /> : null}
          {props.repo.key ? <Badge color="gray" variant="outline" size="xs">{props.repo.key}</Badge> : null}
        </Group>
        <Group gap={6} wrap="nowrap" miw={0}>
          <IconFolder className="repo-path-icon" size={13} />
          <Text c="dimmed" size="xs" className="truncate">
            {props.repo.path || "Select a checked-out git folder to lock the repository path."}
          </Text>
        </Group>
      </Box>
      <Group className="repo-actions" gap="xs" wrap="nowrap">
        {!pathLocked ? (
          <Button leftSection={<IconFolder size={14} />} onClick={browseFolder} size="compact-sm" variant="light">
            Folder
          </Button>
        ) : (
          <ActionIcon onClick={browseFolder} variant="subtle" size="lg" aria-label="Change repository folder">
            <IconPencil size={15} />
          </ActionIcon>
        )}
        <Select
          aria-label="Base branch"
          className="repo-base-select"
          data={branchOptions}
          disabled={!pathLocked}
          leftSection={<IconGitBranch size={13} />}
          onChange={(value) => update({ base: value ?? props.repo.base })}
          placeholder={pathLocked ? undefined : "Base"}
          searchable
          value={props.repo.base || null}
        />
        <ActionIcon color="red" onClick={props.onRemove} variant="subtle" size="lg" aria-label="Remove repository">
          <IconTrash size={15} />
        </ActionIcon>
      </Group>
    </div>
  );
}

function mergeDetectedRepos(
  existing: RepoDraft[],
  detected: BrowseRepository[]
): RepoDraft[] {
  const usedKeys = new Set(existing.map((repo) => repo.key));
  const detectedByPath = new Map(detected.map((repo) => [repo.path, repo]));
  const next = existing.map((repo) => {
    const detectedRepo = detectedByPath.get(repo.path);
    return detectedRepo ? {
      ...repo,
      github: repo.github || detectedRepo.github || "",
      base: repo.base || detectedRepo.base || "main",
      branches: detectedRepo.branches?.length ? detectedRepo.branches : repo.branches,
    } : repo;
  });
  const existingPaths = new Set(existing.map((repo) => repo.path));
  for (const repo of detected) {
    if (existingPaths.has(repo.path)) {
      continue;
    }
    const key = uniqueRepoKey(repo.key, usedKeys);
    usedKeys.add(key);
    next.push({
      id: crypto.randomUUID(),
      key,
      github: repo.github ?? "",
      path: repo.path,
      base: repo.base ?? "main",
      branches: repo.branches?.length ? repo.branches : [repo.base ?? "main"],
    });
    existingPaths.add(repo.path);
  }
  return next;
}

function branchSelectOptions(repo: RepoDraft) {
  const values = new Set([repo.base, ...(repo.branches ?? [])].filter(Boolean));
  return Array.from(values).map((branch) => ({ value: branch, label: branch }));
}

function repoKeyFromPath(path: string) {
  const name = path.split("/").filter(Boolean).pop()?.replace(/\.git$/, "") ?? "repo";
  return name.replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "repo";
}

function githubRepoUrl(github: string) {
  return github ? `https://github.com/${github.replace(/\.git$/, "")}` : "";
}

function uniqueRepoKey(base: string, used: Set<string>) {
  const root = base || "repo";
  if (!used.has(root)) {
    return root;
  }
  let index = 2;
  while (used.has(`${root}-${index}`)) {
    index += 1;
  }
  return `${root}-${index}`;
}

function FolderPickerModal(props: { target: FolderPickerTarget | null; onClose: () => void }) {
  const [browse, setBrowse] = React.useState<BrowseResponse | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const loadPath = React.useCallback(async (path: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/browse?path=${encodeURIComponent(path)}`, { cache: "no-store" });
      const payload = await response.json() as BrowseResponse;
      setBrowse(payload);
    } catch {
      setError("Could not read that folder.");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (props.target) {
      void loadPath(props.target.path);
    } else {
      setBrowse(null);
      setError(null);
    }
  }, [loadPath, props.target]);

  const selectCurrent = () => {
    if (props.target && browse?.path && (!props.target.requireRepository || browse.current_repository)) {
      props.target.onSelect(browse.path, browse);
      props.onClose();
    }
  };
  const currentSelectionDisabled = !browse?.path || Boolean(props.target?.requireRepository && !browse.current_repository);

  return (
    <Modal
      opened={props.target !== null}
      onClose={props.onClose}
      size="lg"
      title={props.target?.title ?? "Select folder"}
    >
      <Stack gap="sm">
        <Group justify="space-between" gap="sm" wrap="nowrap">
          <Text className="truncate" fw={700} size="sm">{browse?.path ?? props.target?.path ?? ""}</Text>
          <Button disabled={currentSelectionDisabled} onClick={selectCurrent} size="xs">Use folder</Button>
        </Group>
        {props.target?.requireRepository && browse && !browse.current_repository ? (
          <Text c="dimmed" size="xs">Select a folder that contains a `.git` checkout.</Text>
        ) : null}
        {error ? <Text c="red" size="sm">{error}</Text> : null}
        <Paper withBorder className="folder-picker-list">
          <Stack gap={0}>
            {browse?.parent ? (
              <UnstyledButton className="folder-picker-row" onClick={() => void loadPath(browse.parent ?? "")}>
                ..
              </UnstyledButton>
            ) : null}
            {loading ? <Text c="dimmed" p="sm" size="sm">Loading folders...</Text> : null}
            {!loading && browse?.directories.map((directory) => (
              <UnstyledButton
                className="folder-picker-row"
                key={directory.path}
                onClick={() => void loadPath(directory.path)}
              >
                <Group gap="xs" wrap="nowrap">
                  <IconFolder size={15} />
                  <Text className="truncate" size="sm">{directory.name}</Text>
                </Group>
              </UnstyledButton>
            ))}
            {!loading && browse && browse.directories.length === 0 ? (
              <Text c="dimmed" p="sm" size="sm">No child folders.</Text>
            ) : null}
          </Stack>
        </Paper>
      </Stack>
    </Modal>
  );
}
