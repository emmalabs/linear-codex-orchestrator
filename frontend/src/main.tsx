import "@mantine/core/styles.css";
import * as React from "react";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Accordion,
  Anchor,
  AppShell,
  Badge,
  Box,
  Button,
  Card,
  Divider,
  Drawer,
  Group,
  MantineProvider,
  Paper,
  ScrollArea,
  SimpleGrid,
  Stack,
  Tabs,
  Table,
  Text,
  Title,
  UnstyledButton
} from "@mantine/core";
import "./styles.css";

type IssueStatus = {
  identifier?: string;
  title?: string;
  url?: string;
  project?: string;
  project_url?: string;
  workspace_path?: string;
  repos?: Array<{ key: string; github?: string; path?: string; base?: string }> | string;
  changed_repos?: string;
  prs?: string;
  status?: string;
  updated_at?: string;
};

type PullRequestStatus = {
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
};

type StageLog = {
  name: string;
  size: number;
  modified: number;
  summary?: StageLogSummary | null;
};

type StageLogSummary = {
  headline?: string;
  message?: string;
  tokens_used?: number | null;
  file_count?: number;
  files?: Array<{
    path: string;
    added: number;
    removed: number;
  }>;
};

type DashboardData = {
  issues: IssueStatus[];
  prs: PullRequestStatus[];
  tasks: TaskLog[];
  orchestration: string;
  connected: boolean;
  refreshedAt: Date;
};

type TaskLog = {
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

type SelectedDetail =
  | { kind: "issue"; item: IssueStatus }
  | { kind: "pr"; item: PullRequestStatus };

const emptyData: DashboardData = {
  issues: [],
  prs: [],
  tasks: [],
  orchestration: "",
  connected: false,
  refreshedAt: new Date()
};

function App() {
  const [data, setData] = React.useState<DashboardData>(emptyData);
  const [selectedDetail, setSelectedDetail] = React.useState<SelectedDetail | null>(null);
  const orchestrationRef = React.useRef<HTMLPreElement | null>(null);
  const shouldFollowRef = React.useRef(true);

  React.useEffect(() => {
    let cancelled = false;

    async function refresh() {
      try {
        const [orchestrationResponse, tasksResponse, statusResponse] = await Promise.all([
          fetch("/api/orchestrator", { cache: "no-store" }),
          fetch("/api/tasks", { cache: "no-store" }),
          fetch("/api/status", { cache: "no-store" })
        ]);
        const orchestration = await orchestrationResponse.json() as { text?: string };
        const tasks = await tasksResponse.json() as TaskLog[];
        const status = await statusResponse.json() as {
          issues?: IssueStatus[];
          prs?: PullRequestStatus[];
        };
        if (cancelled) {
          return;
        }
        setData({
          issues: status.issues ?? [],
          prs: status.prs ?? [],
          tasks,
          orchestration: orchestration.text ?? "",
          connected: true,
          refreshedAt: new Date()
        });
      } catch {
        if (!cancelled) {
          setData((current) => ({ ...current, connected: false, refreshedAt: new Date() }));
        }
      }
    }

    void refresh();
    const interval = window.setInterval(refresh, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  React.useLayoutEffect(() => {
    const element = orchestrationRef.current;
    if (element && shouldFollowRef.current) {
      element.scrollTop = element.scrollHeight;
    }
  }, [data.orchestration]);

  return (
    <MantineProvider defaultColorScheme="dark">
      <AppShell header={{ height: 50 }} padding="md">
        <AppShell.Header>
          <Group h="100%" px="md" justify="space-between">
            <Title order={1} size="h3">Linear Codex Orchestrator</Title>
            <Group gap="xs">
              <Badge color={data.connected ? "green" : "red"} variant="light">
                {data.connected ? "Live" : "Disconnected"}
              </Badge>
              <Text c="dimmed" size="sm">{data.refreshedAt.toLocaleTimeString()}</Text>
            </Group>
          </Group>
        </AppShell.Header>

        <AppShell.Main>
          <Stack gap="md">
            <SimpleGrid cols={{ base: 1, sm: 2, lg: 5 }} spacing="sm">
              <MetricCard label="Current step" value={currentStep(data.orchestration).label} detail={currentStep(data.orchestration).detail} />
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
                <Paper withBorder>
                  <Tabs defaultValue="orchestration">
                    <Tabs.List>
                      <Tabs.Tab value="orchestration">Orchestration</Tabs.Tab>
                    </Tabs.List>
                    <Tabs.Panel value="orchestration" p="md">
                      <pre
                        ref={orchestrationRef}
                        className="orchestration-log"
                        onScroll={(event) => {
                          const element = event.currentTarget;
                          const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
                          shouldFollowRef.current = distanceFromBottom < 24;
                        }}
                      >
                        {data.orchestration || "No orchestration log yet."}
                      </pre>
                    </Tabs.Panel>
                  </Tabs>
                </Paper>
              </Stack>

              <Paper withBorder className="side-panel">
                <Tabs defaultValue="issues" keepMounted={false}>
                  <Tabs.List grow>
                    <Tabs.Tab value="issues">Linear Issues</Tabs.Tab>
                    <Tabs.Tab value="prs">Pull Requests</Tabs.Tab>
                  </Tabs.List>
                  <Tabs.Panel value="issues">
                    <ScrollArea.Autosize mah="calc(100vh - 190px)" type="auto">
                      <SummaryList emptyText="No issue status yet.">
                        {data.issues.map((issue) => (
                          <IssueItem
                            issue={issue}
                            key={issue.identifier ?? issue.url ?? issue.title}
                            onOpen={() => setSelectedDetail({ kind: "issue", item: issue })}
                          />
                        ))}
                      </SummaryList>
                    </ScrollArea.Autosize>
                  </Tabs.Panel>
                  <Tabs.Panel value="prs">
                    <ScrollArea.Autosize mah="calc(100vh - 190px)" type="auto">
                      <SummaryList emptyText="No PR status yet.">
                        {data.prs.map((pr) => (
                          <PullRequestItem
                            key={pr.key ?? pr.url ?? pr.title}
                            onOpen={() => setSelectedDetail({ kind: "pr", item: pr })}
                            pr={pr}
                          />
                        ))}
                      </SummaryList>
                    </ScrollArea.Autosize>
                  </Tabs.Panel>
                </Tabs>
              </Paper>
            </Box>
          </Stack>
        </AppShell.Main>
      </AppShell>
      <DetailDrawer
        detail={selectedDetail}
        onClose={() => setSelectedDetail(null)}
        tasks={data.tasks}
      />
    </MantineProvider>
  );
}

function MetricCard(props: { label: string; value: string | number; detail: string }) {
  return (
    <Card withBorder padding="sm">
      <Text c="dimmed" fw={700} size="xs" tt="uppercase">{props.label}</Text>
      <Text fw={750} size="xl" lh={1.2}>{props.value}</Text>
      <Text c="dimmed" className="truncate" size="sm">{props.detail}</Text>
    </Card>
  );
}

function SummaryList(props: { emptyText: string; children: React.ReactNode }) {
  const children = React.Children.toArray(props.children);
  if (!children.length) {
    return <Text c="dimmed" p="sm" size="sm">{props.emptyText}</Text>;
  }
  return <Stack gap={0}>{children}</Stack>;
}

function IssueItem({ issue, onOpen }: { issue: IssueStatus; onOpen: () => void }) {
  return (
    <UnstyledButton className="summary-row" onClick={onOpen}>
      <Group align="flex-start" justify="space-between" gap="sm" wrap="nowrap">
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
      </Group>
    </UnstyledButton>
  );
}

function PullRequestItem({ pr, onOpen }: { pr: PullRequestStatus; onOpen: () => void }) {
  return (
    <UnstyledButton className="summary-row" onClick={onOpen}>
      <Group align="flex-start" justify="space-between" gap="sm" wrap="nowrap">
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
      </Group>
    </UnstyledButton>
  );
}

function StatusPill({ status }: { status?: string }) {
  return <Badge color="gray" radius="xl" size="sm" variant="outline">{status || "Unknown"}</Badge>;
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

function DetailDrawer(props: {
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
            {prs[0] ? <Button component="a" href={prs[0]} target="_blank" rel="noopener noreferrer" size="xs" variant="light">PR</Button> : null}
            {issue.project_url ? <Button component="a" href={issue.project_url} target="_blank" rel="noopener noreferrer" size="xs" variant="light">Project</Button> : null}
            {issue.url ? <Button component="a" href={issue.url} target="_blank" rel="noopener noreferrer" size="xs">Linear</Button> : null}
          </Group>
        </Group>
        <SimpleGrid cols={{ base: 1, sm: 2, md: 4 }} spacing="sm">
          <DetailTile label="Project" value={issue.project} href={issue.project_url} />
          <DetailTile label="Changed repos" value={issue.changed_repos ?? legacyChangedRepos(issue)} />
          <DetailTile label="Updated" value={issue.updated_at} />
          <DetailTile label="Workspace" value={issue.workspace_path} />
        </SimpleGrid>
        {prs.length ? (
          <Box>
            <Text c="dimmed" size="sm">Pull requests</Text>
            <Stack gap={4} mt={4}>
              {prs.map((pr) => (
                <ExternalLink href={pr} key={pr}>{prLabel(pr)}</ExternalLink>
              ))}
            </Stack>
          </Box>
        ) : null}
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

function DetailTile({ label, value, href }: { label: string; value?: string; href?: string }) {
  return (
    <Paper withBorder p="sm">
      <Text c="dimmed" fw={700} size="xs" tt="uppercase">{label}</Text>
      {value ? (
        href ? <ExternalLink href={href}>{value}</ExternalLink> : <Text className="truncate" size="sm">{value}</Text>
      ) : (
        <Text c="dimmed" size="sm">-</Text>
      )}
    </Paper>
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
          {pr.url ? <Button component="a" href={pr.url} target="_blank" rel="noopener noreferrer" size="xs">GitHub</Button> : null}
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
          <RawLogViewer name={log.name} />
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

function RawLogViewer({ name }: { name: string }) {
  const [raw, setRaw] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");

  async function loadRaw() {
    if (raw !== null || loading) {
      return;
    }
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`/logs/${encodeURIComponent(name)}`, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      setRaw(await response.text());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Unable to load raw log");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Accordion variant="separated" onChange={(value) => {
      if (value === "raw") {
        void loadRaw();
      }
    }}>
      <Accordion.Item value="raw">
        <Accordion.Control>Raw output</Accordion.Control>
        <Accordion.Panel>
          <Stack gap="xs">
            {raw === null && !error ? (
              <Button loading={loading} onClick={() => void loadRaw()} size="xs" variant="light">
                Load raw log
              </Button>
            ) : null}
            {error ? <Text c="red" size="sm">{error}</Text> : null}
            {raw !== null ? (
              <ScrollArea.Autosize mah={360} type="auto">
                <pre className="raw-log-viewer">{raw}</pre>
              </ScrollArea.Autosize>
            ) : null}
          </Stack>
        </Accordion.Panel>
      </Accordion.Item>
    </Accordion>
  );
}

function ExternalLink({ href, children }: { href?: string; children: React.ReactNode }) {
  if (!href) {
    return <span>{children}</span>;
  }
  return (
    <Anchor
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      fw={700}
      onClick={(event) => event.stopPropagation()}
    >
      {children}
    </Anchor>
  );
}

function latestStatus(items: Array<{ status?: string; updated_at?: string }>) {
  if (!items.length) {
    return "None tracked";
  }
  const latest = items[0];
  return `${latest.status || "Unknown"} - ${latest.updated_at || "no timestamp"}`;
}

function latestTask(tasks: TaskLog[]) {
  if (!tasks.length) {
    return "No task logs yet";
  }
  return `${tasks[0].title} - ${tasks[0].log_count} log(s)`;
}

function formatBytes(size: number) {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function formatCount(value?: number | null) {
  if (value === undefined || value === null) {
    return "-";
  }
  const rounded = Math.round(value);
  if (Math.abs(rounded) < 1000) {
    return rounded.toString();
  }
  if (Math.abs(rounded) < 1_000_000) {
    return `${trimFixed(rounded / 1000)}k`;
  }
  return `${trimFixed(rounded / 1_000_000)}M`;
}

function trimFixed(value: number) {
  return value.toFixed(1).replace(/\.0$/, "");
}

function issueRepos(issue: IssueStatus) {
  return Array.isArray(issue.repos) ? issue.repos : [];
}

function legacyChangedRepos(issue: IssueStatus) {
  return typeof issue.repos === "string" ? issue.repos : undefined;
}

function issuePullRequests(issue: IssueStatus) {
  if (!issue.prs) {
    return [];
  }
  return issue.prs
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function prLabel(url: string) {
  const match = url.match(/github\.com\/([^/]+\/[^/]+)\/pull\/(\d+)/);
  return match ? `${match[1]}#${match[2]}` : url;
}

function tasksForDetail(detail: SelectedDetail, tasks: TaskLog[]) {
  const keys = detail.kind === "issue" ? issueTaskKeys(detail.item) : prTaskKeys(detail.item);
  const matched = tasks.filter((task) => keys.has(task.key.toLowerCase()));
  return matched.length ? matched : fallbackIssueTasks(detail, tasks);
}

function issueTaskKeys(issue: IssueStatus) {
  const keys = new Set<string>();
  if (issue.identifier) {
    keys.add(issue.identifier.toLowerCase().replace("_", "-"));
  }
  return keys;
}

function prTaskKeys(pr: PullRequestStatus) {
  const keys = new Set<string>();
  const number = prNumber(pr);
  if (number && pr.repo_key) {
    keys.add(`${pr.repo_key}-${number}`.toLowerCase());
  }
  const issue = issueIdentifier(pr.title || "") || pr.issue;
  if (issue) {
    keys.add(issue.toLowerCase());
  }
  return keys;
}

function fallbackIssueTasks(detail: SelectedDetail, tasks: TaskLog[]) {
  const issue = detail.kind === "issue"
    ? detail.item.identifier
    : issueIdentifier(detail.item.title || "") || detail.item.issue;
  if (!issue) {
    return [];
  }
  const normalized = issue.toLowerCase();
  return tasks.filter((task) => task.key.toLowerCase() === normalized);
}

function issueIdentifier(value: string) {
  return value.match(/\b[A-Z]+-\d+\b/i)?.[0];
}

function prNumber(pr: PullRequestStatus) {
  const fromKey = pr.key?.match(/#(\d+)$/)?.[1];
  if (fromKey) {
    return fromKey;
  }
  return pr.url?.match(/\/pull\/(\d+)(?:$|[/?#])/)?.[1];
}

function stageName(name: string) {
  const stem = name.replace(/\.log$/, "");
  const body = stem.replace(/^\d{8}-\d{6}-/, "");
  return body
    .split("-")
    .map((part) => part ? part[0].toUpperCase() + part.slice(1) : part)
    .join(" ");
}

function currentStep(orchestration: string) {
  const line = orchestration
    .split("\n")
    .map((item) => item.trim())
    .reverse()
    .find((item) => item && !item.startsWith("====="));
  if (!line) {
    return { label: "Idle", detail: "No orchestration log yet" };
  }
  const match = line.match(/^\[([^\]]+)\]\s*(.*)$/);
  if (!match) {
    return { label: "Working", detail: line };
  }
  return {
    label: stepLabel(match[2]),
    detail: `${match[1]} - ${match[2]}`
  };
}

function stepLabel(message: string) {
  if (/failed/i.test(message)) return "Failed";
  if (/implementation started|implementing/i.test(message)) return "Implementing";
  if (/optimization started|optimizing/i.test(message)) return "Optimizing";
  if (/review started|reviewing/i.test(message)) return "Reviewing";
  if (/planning/i.test(message)) return "Planning";
  if (/reading full Linear issue context/i.test(message)) return "Reading Linear";
  if (/checking open PRs|PR feedback/i.test(message)) return "Checking PRs";
  if (/polling Linear/i.test(message)) return "Polling Linear";
  if (/sleeping/i.test(message)) return "Sleeping";
  if (/daemon started/i.test(message)) return "Started";
  return "Working";
}

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <App />
  </StrictMode>
);
