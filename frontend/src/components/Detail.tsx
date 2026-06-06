import * as React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { IconArrowLeft, IconBrandGithub, IconExternalLink, IconFolder } from "@tabler/icons-react";
import {
  Accordion,
  Badge,
  Box,
  Button,
  Group,
  Paper,
  SimpleGrid,
  Stack,
  Table,
  Text,
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
  onBack: () => void;
}) {
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
        <Text c="dimmed" size="xs">{props.detail.kind === "issue" ? "Issue" : "Pull request"}</Text>
      </Group>

      <Box className="detail-layout">
        <Stack className="detail-main" gap="md" miw={0}>
          <Paper className="detail-title-panel" p="lg">
            <Stack gap="sm">
              <Group gap="xs" wrap="nowrap">
                <Text className="detail-key" fw={800}>{key}</Text>
                <StatusPill status={props.detail.item.status} />
              </Group>
              <Title className="detail-title" order={1}>{title}</Title>
            </Stack>
          </Paper>

          {props.detail.kind === "issue" ? (
            <IssueDetails issue={props.detail.item} />
          ) : (
            <PullRequestDetails pr={props.detail.item} />
          )}

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

        <DetailProperties detail={props.detail} />
      </Box>
    </Stack>
  );
}

function IssueDetails({ issue }: { issue: IssueStatus }) {
  const repos = issueRepos(issue);
  const prs = issuePullRequests(issue);
  return (
    <Paper className="detail-content-card" p="md">
      <Stack gap="md">
        <Group gap="xs">
          {prs[0] ? <Button component="a" href={prs[0]} leftSection={<IconBrandGithub size={14} />} target="_blank" rel="noopener noreferrer" size="xs" variant="light">PR</Button> : null}
          {issue.project_url ? <Button component="a" href={issue.project_url} leftSection={<IconFolder size={14} />} target="_blank" rel="noopener noreferrer" size="xs" variant="light">Project</Button> : null}
          {issue.url ? <Button component="a" href={issue.url} leftSection={<IconExternalLink size={14} />} target="_blank" rel="noopener noreferrer" size="xs">Linear</Button> : null}
        </Group>
        <SimpleGrid cols={{ base: 1, sm: 2, md: 4 }} spacing="sm">
          <DetailTile label="Project" value={issue.project} href={issue.project_url} />
          <DetailTile label="Pull request">
            {prs.length ? (
              <Stack gap={2}>
                {prs.map((pr) => (
                  <ExternalLink href={pr} key={pr}>{prLabel(pr)}</ExternalLink>
                ))}
              </Stack>
            ) : null}
          </DetailTile>
          <DetailTile label="Changed repos" value={issue.changed_repos ?? legacyChangedRepos(issue)} />
          <DetailTile label="Updated" value={issue.updated_at} />
        </SimpleGrid>
        {repos.length ? (
          <Box>
            <Text c="dimmed" size="sm">Repositories</Text>
            <Stack gap={4} mt={4}>
              {repos.map((repo) => (
                <Text className="truncate" key={repo.key} size="sm">
                  {[repo.key, repo.github, repo.path, repo.base].filter(Boolean).join(" · ")}
                </Text>
              ))}
            </Stack>
          </Box>
        ) : null}
      </Stack>
    </Paper>
  );
}

function PullRequestDetails({ pr }: { pr: PullRequestStatus }) {
  return (
    <Paper className="detail-content-card" p="md">
      <Stack gap="md">
        {pr.url ? <Group><Button component="a" href={pr.url} leftSection={<IconBrandGithub size={14} />} target="_blank" rel="noopener noreferrer" size="xs">GitHub</Button></Group> : null}
        <SimpleGrid cols={{ base: 1, sm: 2, md: 4 }} spacing="sm">
          <DetailTile label="Issue" value={pr.issue} />
          <DetailTile label="Repo key" value={pr.repo_key} />
          <DetailTile label="Feedback" value={pr.feedback_count?.toString()} />
          <DetailTile label="Updated" value={pr.updated_at} />
          <DetailTile label="Repository" value={pr.repo} />
          <DetailTile label="Branch" value={pr.branch} />
          <DetailTile label="Base" value={pr.base} />
          <DetailTile label="Repo path" value={pr.repo_path} />
        </SimpleGrid>
      </Stack>
    </Paper>
  );
}

function DetailProperties({ detail }: { detail: SelectedDetail }) {
  if (detail.kind === "issue") {
    const issue = detail.item;
    const prs = issuePullRequests(issue);
    return (
      <Paper className="detail-properties" p="md">
        <Stack gap="sm">
          <Text fw={800}>Properties</Text>
          <DetailTile label="Status" value={issue.status} />
          <DetailTile label="Project" value={issue.project} href={issue.project_url} />
          <DetailTile label="Updated" value={issue.updated_at} />
          <DetailTile label="Changed repos" value={issue.changed_repos ?? legacyChangedRepos(issue)} />
          <DetailTile label="Pull requests">
            {prs.length ? (
              <Stack gap={2}>
                {prs.map((pr) => (
                  <ExternalLink href={pr} key={pr}>{prLabel(pr)}</ExternalLink>
                ))}
              </Stack>
            ) : null}
          </DetailTile>
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
      </Stack>
    </Paper>
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
    <Box className="timeline-item">
      <span className="timeline-dot" aria-hidden="true" />
      <Stack className="timeline-content" gap="sm">
        <Group align="flex-start" justify="space-between" gap="md" wrap="nowrap">
          <Box miw={0}>
            <Group gap="xs">
              <Text fw={800}>{task.title}</Text>
              <Badge color={task.type === "PR feedback" ? "violet" : "blue"} size="sm" variant="light">{task.type}</Badge>
            </Group>
            <Text c="dimmed" className="timeline-summary" size="sm">{task.headline || "No summary yet."}</Text>
          </Box>
          <Stack align="flex-end" gap={0} miw={92}>
            <Text c="dimmed" size="xs">{new Date(task.modified * 1000).toLocaleTimeString()}</Text>
            <Text c="dimmed" size="xs">{new Date(task.modified * 1000).toLocaleDateString()}</Text>
          </Stack>
        </Group>
        <Group className="timeline-stats" gap="lg">
          <Text c="dimmed" size="xs"><strong>{task.file_count}</strong> files</Text>
          <Text c="dimmed" size="xs"><strong>{formatCount(task.tokens_used)}</strong> tokens</Text>
          <Text c="dimmed" size="xs"><strong>{task.log_count}</strong> logs</Text>
        </Group>
        <Accordion className="timeline-stages" variant="separated" multiple>
          {task.stages.map((stage) => (
            <StageLogPanel key={stage.name} log={stage} />
          ))}
        </Accordion>
      </Stack>
    </Box>
  );
}

function StageLogPanel({ log }: { log: StageLog }) {
  return (
    <Accordion.Item value={log.name}>
      <Accordion.Control>
        <Group justify="space-between" wrap="nowrap">
          <Box miw={0}>
            <Text fw={700} size="sm">{stageName(log.name)}</Text>
            <Text c="dimmed" className="truncate" size="xs">
              {log.summary?.headline || "No processed summary."}
            </Text>
            {log.summary?.last_line ? (
              <Text className="truncate" size="xs">
                {log.summary.status === "running" ? "Live: " : "Last: "}
                {log.summary.last_line}
              </Text>
            ) : null}
          </Box>
          <Stack align="flex-end" gap={0} miw={92}>
            <Text c="dimmed" size="xs">{formatCount(log.summary?.tokens_used)} tokens</Text>
            <Text c="dimmed" size="xs">{new Date(log.modified * 1000).toLocaleTimeString()}</Text>
          </Stack>
        </Group>
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
  );
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
