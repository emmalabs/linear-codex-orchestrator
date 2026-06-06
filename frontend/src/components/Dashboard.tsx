import * as React from "react";
import { IconExternalLink, IconTrash } from "@tabler/icons-react";
import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Group,
  Modal,
  Paper,
  ScrollArea,
  Select,
  SimpleGrid,
  Stack,
  Text,
  UnstyledButton
} from "@mantine/core";
import type { DashboardData, IssueStatus, PullRequestStatus, SelectedDetail, TaskLog, WorkspaceMap } from "../types";
import { formatBytes, relativeTime, statusGroup, statusTone, type StatusGroup, type StatusTone } from "../lib/format";
import { currentStep, logLineKind, stageName } from "../lib/orchestration";
import { currentLiveActivity } from "../lib/tasks";
import { StatusPill } from "./common";

type FeedMode = "issues" | "prs";
type FeedFilter = "All" | StatusGroup | "Archived";

const allWorkspaces = "__all__";
const filterOrder: FeedFilter[] = ["All", "Active", "Needs attention", "Ready", "Done", "Archived"];
const groupOrder: StatusGroup[] = ["Active", "Needs attention", "Ready", "Done"];

export function DashboardView(props: {
  data: DashboardData;
  mode: FeedMode;
  onArchive: (detail: SelectedDetail) => Promise<void>;
  onSelectDetail: (detail: SelectedDetail) => void;
  orchestrationRef: React.RefObject<HTMLDivElement | null>;
  shouldFollowRef: React.MutableRefObject<boolean>;
  workspaceMap?: WorkspaceMap;
}) {
  return (
    <Box className="command-grid">
      <FeedPanel
        data={props.data}
        mode={props.mode}
        onArchive={props.onArchive}
        onSelectDetail={props.onSelectDetail}
        workspaceMap={props.workspaceMap}
      />
      <RightRail
        data={props.data}
        orchestrationRef={props.orchestrationRef}
        shouldFollowRef={props.shouldFollowRef}
      />
    </Box>
  );
}

function FeedPanel(props: {
  data: DashboardData;
  mode: FeedMode;
  onArchive: (detail: SelectedDetail) => Promise<void>;
  onSelectDetail: (detail: SelectedDetail) => void;
  workspaceMap?: WorkspaceMap;
}) {
  const [filter, setFilter] = React.useState<FeedFilter>("All");
  const [workspaceFilter, setWorkspaceFilter] = React.useState(allWorkspaces);
  const workspaceCatalog = React.useMemo(
    () => workspaceCatalogFromConfig(props.workspaceMap),
    [props.workspaceMap]
  );
  const items = React.useMemo(
    () => {
      if (props.mode === "issues") {
        return [
          ...props.data.issues.map((item) => issueFeedItem(item, workspaceCatalog, props.onArchive, props.onSelectDetail)),
          ...props.data.archivedIssues.map((item) => issueFeedItem(item, workspaceCatalog, props.onArchive, props.onSelectDetail))
        ];
      }
      return [
        ...props.data.prs.map((item) => prFeedItem(item, workspaceCatalog, props.onArchive, props.onSelectDetail)),
        ...props.data.archivedPrs.map((item) => prFeedItem(item, workspaceCatalog, props.onArchive, props.onSelectDetail))
      ];
    },
    [props.data.archivedIssues, props.data.archivedPrs, props.data.issues, props.data.prs, props.mode, workspaceCatalog, props.onArchive, props.onSelectDetail]
  );
  const workspaceOptions = React.useMemo(
    () => workspaceOptionsFromItems(items),
    [items]
  );
  React.useEffect(() => {
    if (!workspaceOptions.some((option) => option.value === workspaceFilter)) {
      setWorkspaceFilter(allWorkspaces);
    }
  }, [workspaceFilter, workspaceOptions]);
  const filteredItems = React.useMemo(
    () => workspaceFilter === allWorkspaces
      ? items
      : items.filter((item) => item.workspace.value === workspaceFilter),
    [items, workspaceFilter]
  );
  const activeItems = React.useMemo(
    () => filteredItems.filter((item) => !item.archived),
    [filteredItems]
  );
  const archivedItems = React.useMemo(
    () => filteredItems.filter((item) => item.archived),
    [filteredItems]
  );
  const grouped = React.useMemo(() => {
    const buckets = new Map<StatusGroup, FeedItem[]>();
    for (const item of activeItems) {
      const group = statusGroup(item.status);
      if (!buckets.has(group)) {
        buckets.set(group, []);
      }
      buckets.get(group)?.push(item);
    }
    for (const bucket of buckets.values()) {
      bucket.sort((left, right) => timestamp(right.updatedAt) - timestamp(left.updatedAt));
    }
    return buckets;
  }, [activeItems]);
  const counts = React.useMemo(() => {
    const values = new Map<FeedFilter, number>([
      ["All", activeItems.length],
      ["Archived", archivedItems.length]
    ]);
    for (const group of groupOrder) {
      values.set(group, grouped.get(group)?.length ?? 0);
    }
    return values;
  }, [activeItems.length, archivedItems.length, grouped]);
  const visibleGroups = filter === "Archived"
    ? [{ group: "Archived", items: archivedItems }]
    : groupOrder
      .map((group) => ({ group, items: grouped.get(group) ?? [] }))
      .filter((entry) => filter === "All" ? entry.items.length : entry.group === filter);

  return (
    <Paper withBorder className="feed-panel">
      <Group className="feed-header" align="flex-start" justify="space-between" gap="md">
        <Box>
          <Text className="feed-kicker" fw={700} size="xs" tt="uppercase">
            {props.mode === "issues" ? "Issues" : "Pull Requests"}
          </Text>
          <Text fw={800} size="xl">
            {props.mode === "issues" ? "Issues Feed" : "Pull Requests Feed"}
          </Text>
        </Box>
        <Group className="feed-controls" gap="xs" justify="flex-end">
          {workspaceOptions.length > 2 ? (
            <Select
              aria-label="Workspace"
              className="workspace-filter"
              data={workspaceOptions}
              value={workspaceFilter}
              onChange={(value) => setWorkspaceFilter(value ?? allWorkspaces)}
              size="xs"
            />
          ) : null}
          <Group className="feed-filters" gap={6}>
            {filterOrder.map((item) => (
              <UnstyledButton
                className={`feed-filter ${filter === item ? "feed-filter-active" : ""}`}
                key={item}
                onClick={() => setFilter(item)}
              >
                <span>{item}</span>
                <span className="feed-filter-count">{counts.get(item) ?? 0}</span>
              </UnstyledButton>
            ))}
          </Group>
        </Group>
      </Group>

      <ScrollArea.Autosize className="feed-scroll" type="auto">
        {items.length ? (
          <Stack gap="lg">
            {visibleGroups.map(({ group, items: groupItems }) => (
              <Stack className="feed-section" gap="xs" key={group}>
                <Group className="feed-section-title" justify="space-between">
                  <Text fw={800} size="xs" tt="uppercase">{group}</Text>
                  <Text c="dimmed" fw={700} size="xs">[{groupItems.length}]</Text>
                </Group>
                <Stack gap="xs">
                  {groupItems.map((item) => (
                    <FeedRow item={item} key={item.key} />
                  ))}
                </Stack>
              </Stack>
            ))}
            {!visibleGroups.some((entry) => entry.items.length) ? (
              <Text c="dimmed" p="md" size="sm">No items in this filter.</Text>
            ) : null}
          </Stack>
        ) : (
          <Text c="dimmed" p="md" size="sm">
            {props.mode === "issues" ? "No issue status yet." : "No PR status yet."}
          </Text>
        )}
      </ScrollArea.Autosize>
    </Paper>
  );
}

type FeedItem = {
  key: string;
  displayKey: string;
  status?: string;
  title: string;
  url?: string;
  updatedAt?: string;
  meta: string[];
  tone: StatusTone;
  workspace: WorkspaceChoice;
  archived: boolean;
  onArchive?: () => Promise<void>;
  onOpen: () => void;
};

function issueFeedItem(
  issue: IssueStatus,
  workspaceCatalog: WorkspaceCatalogItem[],
  onArchive: (detail: SelectedDetail) => Promise<void>,
  onSelectDetail: (detail: SelectedDetail) => void
): FeedItem {
  const detail: SelectedDetail = { kind: "issue", item: issue };
  const meta = [
    issue.prs ? prMeta(issue.prs) : "",
    issue.changed_repos ? repoMeta(issue.changed_repos) : "",
    issue.project || ""
  ].filter(Boolean);
  return {
    key: issue.identifier ?? issue.url ?? issue.title ?? "issue",
    displayKey: issue.identifier || "Issue",
    status: issue.status,
    title: issue.title || "Untitled issue",
    url: issue.url,
    updatedAt: issue.updated_at,
    meta,
    tone: statusTone(issue.status),
    workspace: workspaceForIssue(issue, workspaceCatalog),
    archived: Boolean(issue.archived),
    onArchive: issue.identifier && !issue.archived ? () => onArchive(detail) : undefined,
    onOpen: () => onSelectDetail(detail)
  };
}

function prFeedItem(
  pr: PullRequestStatus,
  workspaceCatalog: WorkspaceCatalogItem[],
  onArchive: (detail: SelectedDetail) => Promise<void>,
  onSelectDetail: (detail: SelectedDetail) => void
): FeedItem {
  const detail: SelectedDetail = { kind: "pr", item: pr };
  const meta = [
    pr.repo_key || pr.repo || "",
    pr.branch ? `Branch ${pr.branch}` : "",
    pr.base ? `Base ${pr.base}` : "",
    pr.issue ? `Issue ${pr.issue}` : ""
  ].filter(Boolean);
  return {
    key: pr.key ?? pr.url ?? pr.title ?? "pull-request",
    displayKey: pr.key || "Pull request",
    status: pr.status,
    title: pr.title || "Untitled pull request",
    url: pr.url,
    updatedAt: pr.updated_at,
    meta,
    tone: statusTone(pr.status),
    workspace: workspaceForPr(pr, workspaceCatalog),
    archived: Boolean(pr.archived),
    onArchive: pr.key && !pr.archived ? () => onArchive(detail) : undefined,
    onOpen: () => onSelectDetail(detail)
  };
}

type WorkspaceCatalogItem = {
  label: string;
  path?: string;
  value: string;
};

type WorkspaceChoice = {
  label: string;
  value: string;
};

function workspaceCatalogFromConfig(workspaceMap?: WorkspaceMap): WorkspaceCatalogItem[] {
  return Object.entries(workspaceMap ?? {}).map(([teamKey, workspace]) => ({
    label: teamKey,
    path: normalizePath(workspace.path),
    value: `workspace:${teamKey}`
  }));
}

function workspaceOptionsFromItems(items: FeedItem[]) {
  const counts = new Map<string, number>();
  const labels = new Map<string, string>();
  for (const item of items) {
    counts.set(item.workspace.value, (counts.get(item.workspace.value) ?? 0) + 1);
    labels.set(item.workspace.value, item.workspace.label);
  }
  const options = [...counts.entries()]
    .map(([value, count]) => ({ value, label: `${labels.get(value) ?? "Unknown"} (${count})` }))
    .sort((left, right) => left.label.localeCompare(right.label));
  return [{ value: allWorkspaces, label: `All workspaces (${items.length})` }, ...options];
}

function workspaceForIssue(issue: IssueStatus, catalog: WorkspaceCatalogItem[]): WorkspaceChoice {
  const byTeam = issue.team ? catalog.find((workspace) => workspace.label === issue.team) : undefined;
  if (byTeam) {
    return workspaceChoice(byTeam);
  }
  const byPath = workspaceForPath(issue.workspace_path, catalog);
  if (byPath) {
    return workspaceChoice(byPath);
  }
  if (issue.team) {
    return { value: `team:${issue.team}`, label: issue.team };
  }
  if (issue.workspace_path) {
    return { value: `path:${normalizePath(issue.workspace_path)}`, label: pathLabel(issue.workspace_path) };
  }
  return { value: "unknown", label: "Unknown" };
}

function workspaceForPr(pr: PullRequestStatus, catalog: WorkspaceCatalogItem[]): WorkspaceChoice {
  const byPath = workspaceForPath(pr.repo_path, catalog);
  if (byPath) {
    return workspaceChoice(byPath);
  }
  if (pr.repo_key) {
    return { value: `repo:${pr.repo_key}`, label: pr.repo_key };
  }
  return { value: "unknown", label: "Unknown" };
}

function workspaceForPath(path: string | undefined, catalog: WorkspaceCatalogItem[]) {
  const normalized = normalizePath(path);
  if (!normalized) {
    return undefined;
  }
  return catalog.find((workspace) => {
    if (!workspace.path) {
      return false;
    }
    return normalized === workspace.path || normalized.startsWith(`${workspace.path}/`);
  });
}

function workspaceChoice(workspace: WorkspaceCatalogItem): WorkspaceChoice {
  return { value: workspace.value, label: workspace.label };
}

function normalizePath(path: string | undefined) {
  return path?.replace(/\/+$/, "");
}

function pathLabel(path: string) {
  const normalized = normalizePath(path) ?? path;
  return normalized.split("/").filter(Boolean).pop() || normalized;
}

function prMeta(value: string) {
  if (/^https?:\/\//i.test(value)) {
    return "PR linked";
  }
  return `PR ${value}`;
}

function repoMeta(value: string) {
  if (!value || value.toLowerCase() === "none") {
    return "";
  }
  return `Repos ${value}`;
}

function FeedRow({ item }: { item: FeedItem }) {
  const timeLabel = relativeTime(item.updatedAt);
  const [archiveModalOpen, setArchiveModalOpen] = React.useState(false);
  const [isArchiving, setIsArchiving] = React.useState(false);
  const openArchiveModal = (event: React.MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    if (!item.onArchive || isArchiving) {
      return;
    }
    setArchiveModalOpen(true);
  };
  const archiveItem = async () => {
    if (!item.onArchive || isArchiving) {
      return;
    }
    setIsArchiving(true);
    try {
      await item.onArchive();
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "Unable to archive item.");
      setIsArchiving(false);
    }
  };

  return (
    <>
      <UnstyledButton className={`feed-row feed-row-${item.tone}`} onClick={item.onOpen}>
      <span className={`feed-row-accent feed-row-accent-${item.tone}`} aria-hidden="true" />
      <Box className="feed-row-body" miw={0}>
        <Group align="flex-start" justify="space-between" gap="sm" wrap="nowrap">
          <Group gap="xs" miw={0} wrap="nowrap">
            <span className={`status-dot status-dot-${item.tone}`} aria-hidden="true" />
            <Text className="feed-row-key" fw={800} size="sm">{item.displayKey}</Text>
            <StatusPill status={item.status} />
          </Group>
          <Group className="feed-row-actions" gap={4} wrap="nowrap">
            {timeLabel ? <Text c="dimmed" size="xs" title={item.updatedAt}>{timeLabel}</Text> : null}
            {item.url ? (
              <ActionIcon
                aria-label={`Open ${item.displayKey}`}
                component="a"
                href={item.url}
                onClick={(event) => event.stopPropagation()}
                rel="noopener noreferrer"
                size="sm"
                target="_blank"
                variant="subtle"
              >
                <IconExternalLink size={14} />
              </ActionIcon>
            ) : null}
            {item.onArchive ? (
              <ActionIcon
                aria-label={`Archive ${item.displayKey}`}
                className="feed-row-archive"
                color="gray"
                loading={isArchiving}
                onClick={openArchiveModal}
                size="sm"
                title={`Archive ${item.displayKey}`}
                variant="subtle"
              >
                <IconTrash size={14} />
              </ActionIcon>
            ) : null}
          </Group>
        </Group>
        <Text className="feed-row-title" mt={4} size="sm">{item.title}</Text>
        {item.meta.length ? (
          <Group className="feed-meta" gap={6} mt="xs">
            {item.meta.map((value) => (
              <Badge className="feed-meta-chip" color="dark" key={value} radius="sm" size="sm" variant="light">
                {value}
              </Badge>
            ))}
          </Group>
        ) : null}
      </Box>
      </UnstyledButton>
      <Modal
        centered
        opened={archiveModalOpen}
        onClose={() => setArchiveModalOpen(false)}
        size="sm"
        title={`Archive ${item.displayKey}`}
      >
        <Stack gap="md">
          <Text size="sm">
            Remove this item from the local dashboard status list? This will not delete it from Linear or GitHub.
          </Text>
          <Group justify="flex-end" gap="xs">
            <Button disabled={isArchiving} onClick={() => setArchiveModalOpen(false)} size="xs" variant="subtle">
              Cancel
            </Button>
            <Button color="red" loading={isArchiving} onClick={archiveItem} size="xs">
              Archive
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}

function RightRail(props: {
  data: DashboardData;
  orchestrationRef: React.RefObject<HTMLDivElement | null>;
  shouldFollowRef: React.MutableRefObject<boolean>;
}) {
  const step = currentStep(props.data.orchestration);
  return (
    <Stack className="right-rail" gap="md">
      <LiveActivityPanel currentStepLabel={step.label} tasks={props.data.tasks} />

      <Paper withBorder className="rail-panel log-panel" p="md">
        <Group justify="space-between" mb="sm">
          <Text fw={800}>Orchestration Log</Text>
          <Text c="dimmed" size="xs">{props.data.orchestration.split("\n").filter(Boolean).length} lines</Text>
        </Group>
        <div
          ref={props.orchestrationRef}
          className="orchestration-log compact-orchestration-log"
          onScroll={(event) => {
            const element = event.currentTarget;
            const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
            props.shouldFollowRef.current = distanceFromBottom < 24;
          }}
        >
          <OrchestrationLog text={props.data.orchestration} />
        </div>
      </Paper>
    </Stack>
  );
}

function OrchestrationLog({ text }: { text: string }) {
  if (!text) {
    return <div className="log-line log-line-muted">No orchestration log yet.</div>;
  }
  return (
    <>
      {text.split("\n").map((line, index) => (
        <LogLine key={`${index}-${line}`} line={line} />
      ))}
    </>
  );
}

function LogLine({ line }: { line: string }) {
  if (!line) {
    return <div className="log-line">&nbsp;</div>;
  }
  if (line.startsWith("=====")) {
    return <div className="log-line log-line-session">{line}</div>;
  }
  const match = line.match(/^\[([^\]]+)\]\s*(.*)$/);
  const kind = logLineKind(line);
  if (!match) {
    return <div className={`log-line ${kind}`}>{line}</div>;
  }
  return (
    <div className={`log-line ${kind}`}>
      <span className="log-time">[{match[1]}]</span>{" "}
      <span>{match[2]}</span>
    </div>
  );
}

function LiveActivityPanel({ currentStepLabel, tasks }: { currentStepLabel: string; tasks: TaskLog[] }) {
  const daemonCanHaveActiveTask = !["Idle", "Sleeping", "Polling Linear", "Checking PRs"].includes(currentStepLabel);
  const activity = daemonCanHaveActiveTask ? currentLiveActivity(tasks) : null;
  return (
    <Paper withBorder px="md" py="sm" className="live-activity-panel rail-panel">
      <Group align="center" justify="space-between" gap="md" wrap="nowrap">
        <Box miw={0}>
          <Group gap="xs">
            <Badge color={activity ? "green" : "gray"} variant="light">
              {activity ? "Running" : "Idle"}
            </Badge>
            <Text fw={700} size="sm">{activity?.task.title ?? "No active task"}</Text>
          </Group>
          <Text c="dimmed" className="truncate live-activity-line" size="xs">
            {activity ? `${stageName(activity.stage.name)}: ${activity.stage.summary?.last_line || "Working"}` : `${currentStepLabel}: waiting for the next Codex stage.`}
          </Text>
        </Box>
        {activity ? (
          <Stack align="flex-end" gap={0} miw={96}>
            <Text c="dimmed" size="xs">{formatBytes(activity.stage.size)}</Text>
            <Text c="dimmed" size="xs">{new Date(activity.stage.modified * 1000).toLocaleTimeString()}</Text>
          </Stack>
        ) : null}
      </Group>
    </Paper>
  );
}

function timestamp(value?: string) {
  if (!value) {
    return 0;
  }
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}
