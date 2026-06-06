import * as React from "react";
import {
  Badge,
  Box,
  Group,
  Paper,
  ScrollArea,
  SimpleGrid,
  Stack,
  Tabs,
  Text,
  UnstyledButton
} from "@mantine/core";
import type { DashboardData, IssueStatus, PullRequestStatus, SelectedDetail, TaskLog } from "../types";
import { formatBytes } from "../lib/format";
import { currentStep, logLineKind, stageName } from "../lib/orchestration";
import { currentLiveActivity, latestStatus, latestTask } from "../lib/tasks";
import { MetricCard, StatusPill, SummaryList } from "./common";

export function DashboardView(props: {
  data: DashboardData;
  onSelectDetail: (detail: SelectedDetail) => void;
  orchestrationRef: React.RefObject<HTMLDivElement | null>;
  shouldFollowRef: React.MutableRefObject<boolean>;
}) {
  const { data } = props;
  const step = currentStep(data.orchestration);
  return (
    <Stack className="dashboard-stack" gap="md">
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 5 }} spacing="sm">
        <MetricCard label="Current step" value={step.label} detail={step.detail} />
        <MetricCard label="Linear issues" value={data.issues.length} detail={latestStatus(data.issues)} />
        <MetricCard label="Pull requests" value={data.prs.length} detail={latestStatus(data.prs)} />
        <MetricCard label="Tasks" value={data.tasks.length} detail={latestTask(data.tasks)} />
        <MetricCard
          label="Daemon"
          value={data.connected ? "Live" : "Offline"}
          detail={data.refreshedAt.toLocaleTimeString()}
        />
      </SimpleGrid>

      <Box className="workspace-grid">
        <Stack gap="md" miw={0}>
          <Paper withBorder className="orchestration-panel">
            <Tabs className="fill-tabs" defaultValue="orchestration">
              <Tabs.List>
                <Tabs.Tab value="orchestration">Orchestration</Tabs.Tab>
              </Tabs.List>
              <Tabs.Panel className="fill-tabs-panel" value="orchestration" p="md">
                <div
                  ref={props.orchestrationRef}
                  className="orchestration-log"
                  onScroll={(event) => {
                    const element = event.currentTarget;
                    const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
                    props.shouldFollowRef.current = distanceFromBottom < 24;
                  }}
                >
                  <OrchestrationLog text={data.orchestration} />
                </div>
              </Tabs.Panel>
            </Tabs>
          </Paper>
        </Stack>

        <Paper withBorder className="side-panel">
          <Tabs defaultValue="issues" keepMounted={false}>
            <Tabs.List grow>
              <Tabs.Tab value="issues">Linear Issues</Tabs.Tab>
              <Tabs.Tab value="prs">Pull Requests</Tabs.Tab>
              <Tabs.Tab value="archive">Archive</Tabs.Tab>
            </Tabs.List>
            <Tabs.Panel value="issues">
              <ScrollArea.Autosize className="side-panel-scroll" type="auto">
                <SummaryList emptyText="No issue status yet.">
                  {data.issues.map((issue) => (
                    <IssueItem
                      issue={issue}
                      key={issue.identifier ?? issue.url ?? issue.title}
                      onOpen={() => props.onSelectDetail({ kind: "issue", item: issue })}
                    />
                  ))}
                </SummaryList>
              </ScrollArea.Autosize>
            </Tabs.Panel>
            <Tabs.Panel value="prs">
              <PullRequestList
                emptyText="No PR status yet."
                prs={data.prs}
                onSelectDetail={props.onSelectDetail}
              />
            </Tabs.Panel>
            <Tabs.Panel value="archive">
              <PullRequestList
                emptyText="No archived PRs yet."
                prs={data.archived_prs}
                onSelectDetail={props.onSelectDetail}
              />
            </Tabs.Panel>
          </Tabs>
        </Paper>
      </Box>
      <LiveActivityPanel tasks={data.tasks} />
    </Stack>
  );
}

function PullRequestList(props: {
  emptyText: string;
  prs: PullRequestStatus[];
  onSelectDetail: (detail: SelectedDetail) => void;
}) {
  return (
    <ScrollArea.Autosize className="side-panel-scroll" type="auto">
      <SummaryList emptyText={props.emptyText}>
        {props.prs.map((pr) => (
          <PullRequestItem
            key={pr.key ?? pr.url ?? pr.title}
            onOpen={() => props.onSelectDetail({ kind: "pr", item: pr })}
            pr={pr}
          />
        ))}
      </SummaryList>
    </ScrollArea.Autosize>
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

function IssueItem({ issue, onOpen }: { issue: IssueStatus; onOpen: () => void }) {
  return (
    <UnstyledRow onOpen={onOpen}>
      <Box miw={0}>
        <Group gap="xs" wrap="nowrap">
          <Text c="blue" fw={700}>{issue.identifier || "Issue"}</Text>
          <StatusPill status={issue.status} />
        </Group>
        <Text c="dimmed" className="truncate" size="sm">{issue.title || "Untitled issue"}</Text>
      </Box>
      <Stack align="flex-end" gap={2} miw={120}>
        <Text className="truncate context-text" c="dimmed" size="sm">
          {issue.project || "No project"}
        </Text>
        <Text c="dimmed" size="xs">{issue.updated_at || ""}</Text>
      </Stack>
    </UnstyledRow>
  );
}

function PullRequestItem({ pr, onOpen }: { pr: PullRequestStatus; onOpen: () => void }) {
  return (
    <UnstyledRow onOpen={onOpen}>
      <Box miw={0}>
        <Group gap="xs" wrap="nowrap">
          <Text c="blue" fw={700}>{pr.key || "Pull request"}</Text>
          <StatusPill status={pr.status} />
        </Group>
        <Text c="dimmed" className="truncate" size="sm">{pr.title || "Untitled pull request"}</Text>
      </Box>
      <Stack align="flex-end" gap={2} miw={88}>
        <Text c="dimmed" size="sm">{pr.repo_key || pr.repo || "Unknown"}</Text>
        <Text c="dimmed" size="xs">{pr.updated_at || ""}</Text>
      </Stack>
    </UnstyledRow>
  );
}

function UnstyledRow(props: { onOpen: () => void; children: React.ReactNode }) {
  return (
    <UnstyledButton className="summary-row" onClick={props.onOpen}>
      <Group align="flex-start" justify="space-between" gap="sm" wrap="nowrap">
        {props.children}
      </Group>
    </UnstyledButton>
  );
}

function LiveActivityPanel({ tasks }: { tasks: TaskLog[] }) {
  const activity = currentLiveActivity(tasks);
  return (
    <Paper withBorder px="sm" py={6} className="live-activity-panel">
      <Group align="center" justify="space-between" gap="md" wrap="nowrap">
        <Box miw={0}>
          <Group gap="xs">
            <Badge color={activity ? "green" : "gray"} variant="light">
              {activity ? "Running" : "Idle"}
            </Badge>
            <Text fw={700} size="sm">{activity?.task.title ?? "No active task"}</Text>
            {activity ? <Text c="dimmed" size="sm">{stageName(activity.stage.name)}</Text> : null}
          </Group>
          <Text c="dimmed" className="truncate live-activity-line" size="xs">
            {activity?.stage.summary?.last_line || "Waiting for the next Codex stage."}
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
