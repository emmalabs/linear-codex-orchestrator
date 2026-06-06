import * as React from "react";
import { IconExternalLink } from "@tabler/icons-react";
import {
  ActionIcon,
  Badge,
  Box,
  Group,
  Paper,
  ScrollArea,
  SimpleGrid,
  Stack,
  Text,
  UnstyledButton
} from "@mantine/core";
import type { DashboardData, IssueStatus, PullRequestStatus, SelectedDetail, TaskLog } from "../types";
import { formatBytes, relativeTime, statusGroup, statusTone, type StatusGroup, type StatusTone } from "../lib/format";
import { currentStep, logLineKind, stageName } from "../lib/orchestration";
import { currentLiveActivity } from "../lib/tasks";
import { StatusPill } from "./common";

type FeedMode = "issues" | "prs";
type FeedFilter = "All" | StatusGroup;

const filterOrder: FeedFilter[] = ["All", "Active", "Needs attention", "Ready", "Done"];
const groupOrder: StatusGroup[] = ["Active", "Needs attention", "Ready", "Done"];

export function DashboardView(props: {
  data: DashboardData;
  mode: FeedMode;
  onSelectDetail: (detail: SelectedDetail) => void;
  orchestrationRef: React.RefObject<HTMLDivElement | null>;
  shouldFollowRef: React.MutableRefObject<boolean>;
}) {
  return (
    <Box className="command-grid">
      <FeedPanel
        data={props.data}
        mode={props.mode}
        onSelectDetail={props.onSelectDetail}
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
  onSelectDetail: (detail: SelectedDetail) => void;
}) {
  const [filter, setFilter] = React.useState<FeedFilter>("All");
  const items = React.useMemo(
    () => props.mode === "issues"
      ? props.data.issues.map((item) => issueFeedItem(item, props.onSelectDetail))
      : props.data.prs.map((item) => prFeedItem(item, props.onSelectDetail)),
    [props.data.issues, props.data.prs, props.mode, props.onSelectDetail]
  );
  const grouped = React.useMemo(() => {
    const buckets = new Map<StatusGroup, FeedItem[]>();
    for (const item of items) {
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
  }, [items]);
  const counts = React.useMemo(() => {
    const values = new Map<FeedFilter, number>([["All", items.length]]);
    for (const group of groupOrder) {
      values.set(group, grouped.get(group)?.length ?? 0);
    }
    return values;
  }, [grouped, items.length]);
  const visibleGroups = groupOrder
    .map((group) => ({ group, items: grouped.get(group) ?? [] }))
    .filter((entry) => filter === "All" ? entry.items.length : entry.group === filter);

  return (
    <Paper withBorder className="feed-panel">
      <Group className="feed-header" align="flex-start" justify="space-between" gap="md">
        <Box>
          <Text className="feed-kicker" fw={700} size="xs" tt="uppercase">
            {props.mode === "issues" ? "Linear Issues" : "Pull Requests"}
          </Text>
          <Text fw={800} size="xl">
            {props.mode === "issues" ? "Linear Issues Feed" : "Pull Requests Feed"}
          </Text>
        </Box>
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
  onOpen: () => void;
};

function issueFeedItem(issue: IssueStatus, onSelectDetail: (detail: SelectedDetail) => void): FeedItem {
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
    onOpen: () => onSelectDetail({ kind: "issue", item: issue })
  };
}

function prFeedItem(pr: PullRequestStatus, onSelectDetail: (detail: SelectedDetail) => void): FeedItem {
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
    onOpen: () => onSelectDetail({ kind: "pr", item: pr })
  };
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
  return (
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
      <Paper withBorder className="rail-panel" p="md">
        <Group justify="space-between" mb="sm">
          <Text fw={800}>System Context</Text>
          <Badge color={props.data.connected ? "green" : "red"} variant="light">
            {props.data.connected ? "Live" : "Offline"}
          </Badge>
        </Group>
        <SimpleGrid cols={2} spacing="xs">
          <ContextTile label="Active Issues" value={props.data.issues.length} />
          <ContextTile label="Tasks" value={props.data.tasks.length} />
          <ContextTile label="Daemon" value={props.data.connected ? "Live" : "Offline"} />
          <ContextTile label="Refresh" value={props.data.refreshedAt.toLocaleTimeString()} />
        </SimpleGrid>
        <Box className="current-step" mt="sm">
          <Text c="dimmed" fw={700} size="xs" tt="uppercase">Current step</Text>
          <Text fw={750} size="sm">{step.label}</Text>
          <Text c="dimmed" className="truncate" size="xs">{step.detail}</Text>
        </Box>
      </Paper>

      <LiveActivityPanel tasks={props.data.tasks} />

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

function ContextTile(props: { label: string; value: string | number }) {
  return (
    <Box className="context-tile">
      <Text c="dimmed" fw={700} size="xs" tt="uppercase">{props.label}</Text>
      <Text className="context-tile-value" fw={800}>{props.value}</Text>
    </Box>
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

function LiveActivityPanel({ tasks }: { tasks: TaskLog[] }) {
  const activity = currentLiveActivity(tasks);
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
            {activity ? `${stageName(activity.stage.name)}: ${activity.stage.summary?.last_line || "Working"}` : "Waiting for the next Codex stage."}
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
