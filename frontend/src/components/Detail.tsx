import * as React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { IconBrandGithub, IconExternalLink, IconFolder } from "@tabler/icons-react";
import {
  Accordion,
  Badge,
  Box,
  Button,
  Card,
  Divider,
  Drawer,
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
import { ExternalLink, MetricCard, StatusPill } from "./common";

export function DetailDrawer(props: {
  detail: SelectedDetail | null;
  tasks: TaskLog[];
  onClose: () => void;
}) {
  const matchingTasks = props.detail ? tasksForDetail(props.detail, props.tasks) : [];
  return (
    <Drawer
      opened={props.detail !== null}
      onClose={props.onClose}
      position="right"
      size="min(1120px, 92vw)"
      title={props.detail?.kind === "issue" ? "Linear issue details" : "Pull request details"}
    >
      {props.detail ? (
        <Stack gap="md">
          {props.detail.kind === "issue" ? (
            <IssueDetails issue={props.detail.item} />
          ) : (
            <PullRequestDetails pr={props.detail.item} />
          )}
          <Divider label="Task logs" labelPosition="left" />
          {matchingTasks.length ? (
            matchingTasks.map((task) => <TaskDetails key={task.key} task={task} />)
          ) : (
            <Text c="dimmed" size="sm">No matching task logs yet.</Text>
          )}
        </Stack>
      ) : null}
    </Drawer>
  );
}

function IssueDetails({ issue }: { issue: IssueStatus }) {
  const repos = issueRepos(issue);
  const prs = issuePullRequests(issue);
  return (
    <Card withBorder padding="md">
      <Stack gap="md">
        <Group align="flex-start" justify="space-between" gap="md">
          <Box miw={0}>
            <Group gap="xs">
              <Title order={2} size="h2">{issue.identifier || "Issue"}</Title>
              <StatusPill status={issue.status} />
            </Group>
            <Text mt={4}>{issue.title || "Untitled issue"}</Text>
          </Box>
          <Group gap="xs">
            {prs[0] ? <Button component="a" href={prs[0]} leftSection={<IconBrandGithub size={14} />} target="_blank" rel="noopener noreferrer" size="xs" variant="light">PR</Button> : null}
            {issue.project_url ? <Button component="a" href={issue.project_url} leftSection={<IconFolder size={14} />} target="_blank" rel="noopener noreferrer" size="xs" variant="light">Project</Button> : null}
            {issue.url ? <Button component="a" href={issue.url} leftSection={<IconExternalLink size={14} />} target="_blank" rel="noopener noreferrer" size="xs">Linear</Button> : null}
          </Group>
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
    </Card>
  );
}

function PullRequestDetails({ pr }: { pr: PullRequestStatus }) {
  return (
    <Card withBorder padding="md">
      <Stack gap="md">
        <Group align="flex-start" justify="space-between" gap="md">
          <Box miw={0}>
            <Group gap="xs">
              <Title order={2} size="h2">{pr.key || "Pull request"}</Title>
              <StatusPill status={pr.status} />
            </Group>
            <Text mt={4}>{pr.title || "Untitled pull request"}</Text>
          </Box>
          {pr.url ? <Button component="a" href={pr.url} leftSection={<IconBrandGithub size={14} />} target="_blank" rel="noopener noreferrer" size="xs">GitHub</Button> : null}
        </Group>
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
    </Card>
  );
}

function DetailTile({ label, value, href, children }: {
  label: string;
  value?: string;
  href?: string;
  children?: React.ReactNode;
}) {
  return (
    <Paper withBorder p="sm">
      <Text c="dimmed" fw={700} size="xs" tt="uppercase">{label}</Text>
      {children ?? (value ? (
        href ? <ExternalLink href={href}>{value}</ExternalLink> : <Text className="truncate" size="sm">{value}</Text>
      ) : (
        <Text c="dimmed" size="sm">-</Text>
      ))}
    </Paper>
  );
}

function TaskDetails({ task }: { task: TaskLog }) {
  return (
    <Card withBorder padding="sm">
      <Stack gap="xs">
        <Group gap="xs">
          <Text fw={700}>{task.title}</Text>
          <Badge color={task.type === "PR feedback" ? "violet" : "blue"} size="sm" variant="light">{task.type}</Badge>
        </Group>
        <Text c="dimmed" size="sm">{task.headline || "No summary yet."}</Text>
        <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="xs">
          <MetricCard label="Changed files" value={task.file_count} detail="Across all stages" />
          <MetricCard label="Tokens" value={formatCount(task.tokens_used)} detail="Across all stages" />
          <MetricCard label="Updated" value={new Date(task.modified * 1000).toLocaleTimeString()} detail={new Date(task.modified * 1000).toLocaleDateString()} />
        </SimpleGrid>
        <Accordion variant="contained" multiple>
          {task.stages.map((stage) => (
            <StageLogPanel key={stage.name} log={stage} />
          ))}
        </Accordion>
      </Stack>
    </Card>
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
