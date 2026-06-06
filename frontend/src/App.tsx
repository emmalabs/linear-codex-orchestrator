import * as React from "react";
import { IconAdjustments, IconBriefcase } from "@tabler/icons-react";
import {
  AppShell,
  Badge,
  Box,
  Group,
  MantineProvider,
  Tabs,
  Text,
  Title
} from "@mantine/core";
import type { ConfigResponse, DashboardData, IssueStatus, PullRequestStatus, SelectedDetail, TaskLog } from "./types";
import { emptyData } from "./types";
import { DashboardView } from "./components/Dashboard";
import { SetupView } from "./components/Setup";
import { DetailDrawer } from "./components/Detail";

export function App() {
  const [data, setData] = React.useState<DashboardData>(emptyData);
  const [configResponse, setConfigResponse] = React.useState<ConfigResponse | null>(null);
  const [activeTab, setActiveTab] = React.useState<string | null>("dashboard");
  const [selectedDetail, setSelectedDetail] = React.useState<SelectedDetail | null>(null);
  const orchestrationRef = React.useRef<HTMLDivElement | null>(null);
  const shouldFollowRef = React.useRef(true);

  const refreshConfig = React.useCallback(async () => {
    const response = await fetch("/api/config", { cache: "no-store" });
    const config = await response.json() as ConfigResponse;
    setConfigResponse(config);
  }, []);

  React.useEffect(() => {
    void refreshConfig();
  }, [refreshConfig]);

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
          archived_prs?: PullRequestStatus[];
        };
        if (cancelled) {
          return;
        }
        setData({
          issues: status.issues ?? [],
          prs: status.prs ?? [],
          archived_prs: status.archived_prs ?? [],
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
      <Tabs className="app-tabs" value={activeTab} onChange={setActiveTab}>
        <AppShell header={{ height: 56 }} padding="md">
          <AppShell.Header>
            <Group className="app-header-content" h="100%" px="md" wrap="nowrap">
              <Title className="app-title" order={1} size="h3">Linear Codex Orchestrator</Title>
              <Tabs.List className="header-tabs">
                <Tabs.Tab value="dashboard">Dashboard</Tabs.Tab>
                <Tabs.Tab leftSection={<IconBriefcase size={14} />} value="workspaces">Workspaces</Tabs.Tab>
                <Tabs.Tab leftSection={<IconAdjustments size={14} />} value="orchestrator">Orchestrator</Tabs.Tab>
              </Tabs.List>
              <Group className="status-group" gap="xs" wrap="nowrap">
                <Badge color={data.connected ? "green" : "red"} variant="light">
                  {data.connected ? "Live" : "Disconnected"}
                </Badge>
                <Text c="dimmed" size="sm">{data.refreshedAt.toLocaleTimeString()}</Text>
              </Group>
            </Group>
          </AppShell.Header>

          <AppShell.Main>
            <Box style={{ display: activeTab === "dashboard" ? "block" : "none" }}>
              <DashboardView
                data={data}
                onSelectDetail={setSelectedDetail}
                orchestrationRef={orchestrationRef}
                shouldFollowRef={shouldFollowRef}
              />
            </Box>
            <Box style={{ display: activeTab === "workspaces" || activeTab === "orchestrator" ? "block" : "none" }}>
              <SetupView
                configResponse={configResponse}
                section={activeTab === "orchestrator" ? "orchestrator" : "workspaces"}
              />
            </Box>
          </AppShell.Main>
        </AppShell>
      </Tabs>
      <DetailDrawer
        detail={selectedDetail}
        onClose={() => setSelectedDetail(null)}
        tasks={data.tasks}
      />
    </MantineProvider>
  );
}
