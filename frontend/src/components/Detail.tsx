import * as React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { IconArchive, IconArrowLeft, IconBrandGithub, IconCopy, IconExternalLink, IconFolder } from "@tabler/icons-react";
import {
  ActionIcon,
  Accordion,
  Badge,
  Box,
  Button,
  Group,
  Modal,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Tooltip,
  Title
} from "@mantine/core";
import type { IssueStatus, PullRequestStatus, SelectedDetail, StageLog, StageLogSummary, TaskLog } from "../types";
import { formatBytes, formatCount } from "../lib/format";
import { stageName } from "../lib/orchestration";
import { issuePullRequests, issueRepos, legacyChangedRepos, prLabel, tasksForDetail } from "../lib/tasks";
import { ExternalLink, StatusPill } from "./common";

export function DetailPage(props: {
  detail: SelectedDetail;
  tasks: TaskLog[];
  onArchive: (detail: SelectedDetail) => Promise<void>;
  onBack: () => void;
  onStatusChange: (detail: SelectedDetail, status: string) => Promise<void>;
}) {
  const [archiveModalOpen, setArchiveModalOpen] = React.useState(false);
  const [isArchiving, setIsArchiving] = React.useState(false);
  const archiveDetail = async () => {
    setIsArchiving(true);
    try {
      await props.onArchive(props.detail);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "Unable to archive item.");
      setIsArchiving(false);
    }
  };
  const matchingTasks = tasksForDetail(props.detail, props.tasks);
  const title = props.detail.kind === "issue"
    ? props.detail.item.title || "Untitled issue"
    : props.detail.item.title || "Untitled pull request";
  const key = props.detail.kind === "issue"
    ? props.detail.item.identifier || "Issue"
    : props.detail.item.key || "Pull request";
  return (
    <Stack className="detail-page" gap="md">
      <Group className="detail-page-toolbar" justify="space-between" gap="md" wrap="nowrap">
        <Button leftSection={<IconArrowLeft size={15} />} onClick={props.onBack} size="xs" variant="subtle">
          Back
        </Button>
        <Group gap="xs">
          {!props.detail.item.archived ? (
            <Button
              color="red"
              leftSection={<IconArchive size={14} />}
              loading={isArchiving}
              onClick={() => setArchiveModalOpen(true)}
              size="xs"
              variant="subtle"
            >
              Archive
            </Button>
          ) : null}
          <Text c="dimmed" size="xs">{props.detail.kind === "issue" ? "Issue" : "Pull request"}</Text>
        </Group>
      </Group>
      <Modal
        centered
        opened={archiveModalOpen}
        onClose={() => setArchiveModalOpen(false)}
        size="sm"
        title={`Archive ${key}`}
      >
        <Stack gap="md">
          <Text size="sm">
            Remove this item from the local dashboard status list? This will not delete it from Linear or GitHub.
          </Text>
          <Group justify="flex-end" gap="xs">
            <Button disabled={isArchiving} onClick={() => setArchiveModalOpen(false)} size="xs" variant="subtle">
              Cancel
            </Button>
            <Button color="red" loading={isArchiving} onClick={archiveDetail} size="xs">
              Archive
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Box className="detail-layout">
        <Stack className="detail-main" gap="md" miw={0}>
          <Paper className="detail-title-panel" p="lg">
            <Stack gap="sm">
              <Group gap="xs" wrap="nowrap">
                <Text className="detail-key" fw={800}>{key}</Text>
                <StatusPill status={props.detail.item.status} />
                {props.detail.item.codex_approved ? <Badge color="green" radius="sm" size="sm" variant="light">👍 Codex approved</Badge> : null}
              </Group>
              <Title className="detail-title" order={1}>{title}</Title>
              <DetailActions
                detail={props.detail}
                onStatusChange={(status) => props.onStatusChange(props.detail, status)}
              />
            </Stack>
          </Paper>

          {props.detail.kind === "issue" ? <IssueBrief issue={props.detail.item} /> : null}

          <Group className="timeline-heading" justify="space-between">
            <Text fw={800}>Task timeline</Text>
            <Text c="dimmed" size="xs">{matchingTasks.length} task{matchingTasks.length === 1 ? "" : "s"}</Text>
          </Group>
          {matchingTasks.length ? (
            <Stack className="task-timeline" gap={0}>
              {matchingTasks.map((task) => <TaskDetails key={task.key} task={task} />)}
            </Stack>
          ) : (
            <Paper className="detail-empty-state" p="md">
              <Text c="dimmed" size="sm">No matching task logs yet.</Text>
            </Paper>
          )}
        </Stack>

        <DetailProperties detail={props.detail} tasks={matchingTasks} />
      </Box>
    </Stack>
  );
}

function DetailActions({ detail, onStatusChange }: {
  detail: SelectedDetail;
  onStatusChange: (status: string) => Promise<void>;
}) {
  const [isUpdating, setIsUpdating] = React.useState(false);
  const [statusValue, setStatusValue] = React.useState(detail.item.status || "");
  React.useEffect(() => {
    setStatusValue(detail.item.status || "");
  }, [detail.item.status]);

  const updateStatus = async (value: string | null) => {
    if (!value || value === statusValue) {
      return;
    }
    const previous = statusValue;
    setStatusValue(value);
    setIsUpdating(true);
    try {
      await onStatusChange(value);
    } catch (error) {
      setStatusValue(previous);
      window.alert(error instanceof Error ? error.message : "Unable to update item status.");
    } finally {
      setIsUpdating(false);
    }
  };

  const statusOptions = statusOptionsFor(detail);

  if (detail.kind === "issue") {
    const issue = detail.item;
    const prs = issuePullRequests(issue);
    return (
      <Group gap="xs" wrap="wrap">
        <Select
          aria-label="Status"
          className="detail-status-select"
          data={statusOptions}
          disabled={isUpdating}
          onChange={updateStatus}
          searchable
          size="xs"
          value={statusValue}
        />
        {prs[0] ? <Button component="a" href={prs[0]} leftSection={<IconBrandGithub size={14} />} target="_blank" rel="noopener noreferrer" size="xs" variant="light">PR</Button> : null}
        {issue.project_url ? <Button component="a" href={issue.project_url} leftSection={<IconFolder size={14} />} target="_blank" rel="noopener noreferrer" size="xs" variant="light">Project</Button> : null}
        {issue.url ? <Button component="a" href={issue.url} leftSection={<IconExternalLink size={14} />} target="_blank" rel="noopener noreferrer" size="xs">Linear</Button> : null}
      </Group>
    );
  }
  return (
    <Group gap="xs" wrap="wrap">
      <Select
        aria-label="Status"
        className="detail-status-select"
        data={statusOptions}
        disabled={isUpdating}
        onChange={updateStatus}
        searchable
        size="xs"
        value={statusValue}
      />
      {detail.item.url ? (
        <Button component="a" href={detail.item.url} leftSection={<IconBrandGithub size={14} />} target="_blank" rel="noopener noreferrer" size="xs">
          GitHub
        </Button>
      ) : null}
    </Group>
  );
}

function statusOptionsFor(detail: SelectedDetail) {
  const base = detail.kind === "issue"
    ? ["Starting", "Planning", "Implementing", "Reviewing", "PR ready", "Codex approved", "Done", "Blocked", "Failed"]
    : ["Open", "Ready", "Codex approved", "No new feedback", "Feedback found", "Fixing feedback", "Merged", "Closed"];
  return uniqueValues([detail.item.status, ...base].filter(Boolean) as string[]);
}

function uniqueValues(values: string[]) {
  return [...new Set(values)].map((value) => ({ value, label: value }));
}

function IssueBrief({ issue }: { issue: IssueStatus }) {
  const plannerBrief = cleanBriefText(issue.planner_brief);
  const issueContext = cleanBriefText(issue.issue_context);
  const description = cleanBriefText(issue.description);
  const prePlanningBrief = issueContext || description;
  const statusLabel = issueBriefStatusLabel(issue.context_status, Boolean(plannerBrief));

  if (!plannerBrief && !prePlanningBrief) {
    return null;
  }

  return (
    <Paper className="issue-brief" p="md">
      <Stack gap="sm">
        <Group justify="space-between" gap="xs">
          <Text fw={800}>Issue brief</Text>
          <Badge color={plannerBrief ? "green" : "cyan"} size="sm" variant="light">{statusLabel}</Badge>
        </Group>
        <Box className="formatted-log-message issue-brief-body">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{plannerBrief || prePlanningBrief}</ReactMarkdown>
        </Box>
      </Stack>
    </Paper>
  );
}

function cleanBriefText(value?: string) {
  return value?.trim() || "";
}

function issueBriefStatusLabel(contextStatus?: string, hasPlannerBrief = false) {
  if (hasPlannerBrief || contextStatus === "planned") {
    return "Planned";
  }
  if (contextStatus === "linear_context") {
    return "Planning";
  }
  return "Preparing";
}

function DetailProperties({ detail, tasks }: { detail: SelectedDetail; tasks: TaskLog[] }) {
  if (detail.kind === "issue") {
    const issue = detail.item;
    const prs = issuePullRequests(issue);
    const metrics = runMetrics(tasks);
    return (
      <Paper className="detail-properties" p="md">
        <Stack gap="sm">
          <Text fw={800}>Properties</Text>
          <BranchDetailTile branch={issue.branch} />
          <DetailTile label="Updated" value={issue.updated_at} />
          <IssueRepositoriesDetailTile issue={issue} />
          <DetailTile label="Pull requests">
            {prs.length ? (
              <Stack gap={2}>
                {prs.map((pr) => (
                  <ExternalLink href={pr} key={pr}>{prLabel(pr)}</ExternalLink>
                ))}
              </Stack>
            ) : null}
          </DetailTile>
          <ApprovalDetails item={issue} />
          <Box className="run-metrics">
            <Text fw={800}>Run metrics</Text>
            <SimpleGrid cols={2} spacing="xs">
              <MetricTile label="Tasks" value={formatCount(metrics.tasks)} />
              <MetricTile label="Logs" value={formatCount(metrics.logs)} />
              <MetricTile label="Files changed" value={formatCount(metrics.filesChanged)} />
              <MetricTile label="Tokens used" value={formatCount(metrics.tokensUsed)} />
            </SimpleGrid>
            <DetailTile label="Last activity" value={metrics.lastActivity} />
          </Box>
        </Stack>
      </Paper>
    );
  }
  const pr = detail.item;
  return (
    <Paper className="detail-properties" p="md">
      <Stack gap="sm">
        <Text fw={800}>Properties</Text>
        <DetailTile label="Status" value={pr.status} />
        <DetailTile label="Issue" value={pr.issue} />
        <DetailTile label="Repository" value={pr.repo_key || pr.repo} />
        <DetailTile label="Branch" value={pr.branch} />
        <DetailTile label="Base" value={pr.base} />
        <DetailTile label="Feedback" value={pr.feedback_count?.toString()} />
        <DetailTile label="Updated" value={pr.updated_at} />
        <ApprovalDetails item={pr} />
      </Stack>
    </Paper>
  );
}

function BranchDetailTile({ branch }: { branch?: string }) {
  const copyBranch = async () => {
    if (!branch) {
      return;
    }
    await navigator.clipboard.writeText(branch);
  };

  return (
    <DetailTile label="Branch">
      {branch ? (
        <Group gap={6} wrap="nowrap">
          <Text className="truncate" size="sm">{branch}</Text>
          <Tooltip label="Copy branch">
            <ActionIcon aria-label="Copy branch" onClick={copyBranch} size="sm" variant="subtle">
              <IconCopy size={14} />
            </ActionIcon>
          </Tooltip>
        </Group>
      ) : (
        <Text c="dimmed" size="sm">-</Text>
      )}
    </DetailTile>
  );
}

function IssueRepositoriesDetailTile({ issue }: { issue: IssueStatus }) {
  const repos = issueRepos(issue);
  const legacyRepos = legacyChangedRepos(issue);
  return (
    <DetailTile label="Repositories">
      {repos.length ? (
        <Stack gap={2}>
          {repos.map((repo) => (
            <Text className="truncate" key={repo.key} size="sm">
              {[repo.key, repo.github, repo.path, repo.base].filter(Boolean).join(" · ")}
            </Text>
          ))}
        </Stack>
      ) : legacyRepos ? (
        <Text className="truncate" size="sm">{legacyRepos}</Text>
      ) : (
        <Text c="dimmed" size="sm">-</Text>
      )}
    </DetailTile>
  );
}

function ApprovalDetails({ item }: { item: IssueStatus | PullRequestStatus }) {
  if (!item.codex_approved) {
    return null;
  }
  return (
    <>
      <DetailTile label="Codex approved" value={item.codex_approved_at} />
      <DetailTile label="Approval review" value={item.codex_approval_url ? "GitHub review" : undefined} href={item.codex_approval_url} />
      <DetailTile label="Approved PR" value={item.codex_approved_pr ? prLabel(item.codex_approved_pr) : undefined} href={item.codex_approved_pr} />
    </>
  );
}

type RunMetrics = {
  tasks: number;
  logs: number;
  filesChanged: number;
  tokensUsed: number;
  lastActivity?: string;
};

function runMetrics(tasks: TaskLog[]): RunMetrics {
  let logs = 0;
  let filesChanged = 0;
  let tokensUsed = 0;
  let lastModified = 0;
  for (const task of tasks) {
    logs += task.log_count || 0;
    filesChanged += task.file_count || 0;
    tokensUsed += task.tokens_used || 0;
    lastModified = Math.max(lastModified, task.modified || 0);
  }
  return {
    tasks: tasks.length,
    logs,
    filesChanged,
    tokensUsed,
    lastActivity: lastModified ? formatTimestamp(lastModified) : undefined
  };
}

function formatTimestamp(seconds: number) {
  const date = new Date(seconds * 1000);
  return `${date.toLocaleTimeString()} · ${date.toLocaleDateString()}`;
}

function MetricTile({ label, value }: { label: string; value: string }) {
  return (
    <Box className="metric-tile">
      <Text c="dimmed" fw={700} size="xs" tt="uppercase">{label}</Text>
      <Text fw={800} size="lg">{value}</Text>
    </Box>
  );
}

function DetailTile({ label, value, href, children }: {
  label: string;
  value?: string;
  href?: string;
  children?: React.ReactNode;
}) {
  return (
    <Box className="detail-tile">
      <Text c="dimmed" fw={700} size="xs" tt="uppercase">{label}</Text>
      {children ?? (value ? (
        href ? <ExternalLink href={href}>{value}</ExternalLink> : <Text className="truncate" size="sm">{value}</Text>
      ) : (
        <Text c="dimmed" size="sm">-</Text>
      ))}
    </Box>
  );
}

function TaskDetails({ task }: { task: TaskLog }) {
  return (
    <Stack className="timeline-task" gap="md">
      <Stack className="timeline-task-description" gap="sm">
        <Box miw={0}>
          <Group gap="xs">
            <Text fw={800}>{task.title}</Text>
            <Badge color={task.type === "PR feedback" ? "violet" : "blue"} size="sm" variant="light">{task.type}</Badge>
          </Group>
          <Text c="dimmed" className="timeline-summary" size="sm">{task.headline || "No summary yet."}</Text>
        </Box>
      </Stack>
      <Stack className="timeline-stage-list" gap={0}>
        {task.stages.map((stage) => (
          <StageTimelineItem key={stage.name} log={stage} />
        ))}
      </Stack>
    </Stack>
  );
}

function StageTimelineItem({ log }: { log: StageLog }) {
  const tone = stageTone(log.name);
  const label = stageToneLabel(tone);
  return (
    <Box className={`timeline-item timeline-item-${tone}`}>
      <span className={`timeline-dot timeline-dot-${tone}`} aria-hidden="true" />
      <Accordion className={`timeline-stage-toggle timeline-stage-toggle-${tone}`} variant="separated">
        <Accordion.Item value={log.name}>
          <Accordion.Control>
            <Stack gap="xs">
              <Group justify="space-between" wrap="nowrap">
                <Box miw={0}>
                  <Group gap="xs" wrap="nowrap">
                    <Text fw={700} size="sm">{stageName(log.name)}</Text>
                    <Badge className={`timeline-stage-badge timeline-stage-badge-${tone}`} size="xs" variant="light">
                      {label}
                    </Badge>
                  </Group>
                  <Text c="dimmed" className="truncate" size="xs">
                    {log.summary?.headline || "No processed summary."}
                  </Text>
                </Box>
                <Stack align="flex-end" gap={0} miw={92}>
                  <Text c="dimmed" size="xs">{formatCount(log.summary?.tokens_used)} tokens</Text>
                  <Text c="dimmed" size="xs">{new Date(log.modified * 1000).toLocaleTimeString()}</Text>
                </Stack>
              </Group>
              {log.summary?.last_line ? (
                <Text className="truncate" size="xs">
                  {log.summary.status === "running" ? "Live: " : "Last: "}
                  {log.summary.last_line}
                </Text>
              ) : null}
            </Stack>
          </Accordion.Control>
          <Accordion.Panel>
            <Stack gap="sm">
              <Group justify="space-between" gap="xs">
                <Text c="dimmed" size="xs">{formatBytes(log.size)} raw log</Text>
                <ExternalLink href={`/logs/${encodeURIComponent(log.name)}`}>Open raw in new tab</ExternalLink>
              </Group>
              <LogMessage message={log.summary?.message} />
              <ChangedFilesTable summary={log.summary} />
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>
      </Accordion>
    </Box>
  );
}

type StageTone = "plan" | "build" | "improve" | "review" | "feedback" | "neutral";

function stageTone(name: string): StageTone {
  if (/planner|planning/i.test(name)) {
    return "plan";
  }
  if (/implementation|implement/i.test(name)) {
    return "build";
  }
  if (/optimization|optimiz/i.test(name)) {
    return "improve";
  }
  if (/review-fix|pr-feedback|feedback/i.test(name)) {
    return "feedback";
  }
  if (/review/i.test(name)) {
    return "review";
  }
  return "neutral";
}

function stageToneLabel(tone: StageTone) {
  return {
    plan: "Plan",
    build: "Build",
    improve: "Improve",
    review: "Review",
    feedback: "Feedback",
    neutral: "Stage"
  }[tone];
}

function LogMessage({ message }: { message?: string }) {
  if (!message) {
    return <Text c="dimmed" size="sm">No final message captured.</Text>;
  }
  return (
    <Box>
      <Text c="dimmed" size="sm">Final message</Text>
      <Box className="formatted-log-message">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{message}</ReactMarkdown>
      </Box>
    </Box>
  );
}

function ChangedFilesTable({ summary }: { summary?: StageLogSummary | null }) {
  const files = summary?.files ?? [];
  if (!files.length) {
    return <Text c="dimmed" size="sm">No changed files detected.</Text>;
  }
  return (
    <Box>
      <Text c="dimmed" size="sm">Changed files</Text>
      <Table.ScrollContainer minWidth={420}>
        <Table striped withTableBorder withColumnBorders>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>File</Table.Th>
              <Table.Th>Added</Table.Th>
              <Table.Th>Removed</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {files.map((file) => (
              <Table.Tr key={file.path}>
                <Table.Td><Text className="truncate" size="sm">{file.path}</Text></Table.Td>
                <Table.Td><Text c="green" size="sm">+{file.added}</Text></Table.Td>
                <Table.Td><Text c="red" size="sm">-{file.removed}</Text></Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Box>
  );
}
