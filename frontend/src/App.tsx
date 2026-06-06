import * as React from "react";
import {
  IconAdjustments,
  IconBook,
  IconBrandGithub,
  IconBriefcase,
  IconGitPullRequest,
  IconListDetails,
  IconPlus
} from "@tabler/icons-react";
import {
  Anchor,
  AppShell,
  Badge,
  Box,
  Button,
  Group,
  MantineProvider,
  Stack,
  Text,
  Title,
  UnstyledButton
} from "@mantine/core";
import type { ConfigResponse, DashboardData, IssueStatus, PullRequestStatus, SelectedDetail, TaskLog } from "./types";
import { emptyData } from "./types";
import { DashboardView } from "./components/Dashboard";
import { SetupView } from "./components/Setup";
import { DetailDrawer } from "./components/Detail";

type ActiveSection = "issues" | "workspaces" | "prs" | "settings";

export function App() {
  const [data, setData] = React.useState<DashboardData>(emptyData);
  const [configResponse, setConfigResponse] = React.useState<ConfigResponse | null>(null);
  const [activeTab, setActiveTab] = React.useState<ActiveSection>("issues");
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

  const activeTitle = activeTab === "issues"
    ? "Issues"
    : activeTab === "prs"
      ? "Pull Requests"
      : activeTab === "workspaces"
        ? "Workspaces"
        : "Settings";

  return (
    <MantineProvider defaultColorScheme="dark">
      <AppShell
        header={{ height: 56 }}
        navbar={{ width: 248, breakpoint: "sm" }}
        padding="md"
      >
        <AppShell.Header>
          <Group className="app-header-content" h="100%" px="md" wrap="nowrap">
            <Box miw={0}>
              <Title className="app-title" order={1} size="h3">{activeTitle}</Title>
              <Text c="dimmed" className="topbar-subtitle" size="xs">
                Command Center
              </Text>
            </Box>
            <Group className="status-group" gap="xs" wrap="nowrap">
              <Badge color={data.connected ? "green" : "red"} variant="light">
                {data.connected ? "Live" : "Disconnected"}
              </Badge>
              <Text c="dimmed" size="sm">{data.refreshedAt.toLocaleTimeString()}</Text>
            </Group>
          </Group>
        </AppShell.Header>

        <AppShell.Navbar className="app-navbar" p="md">
          <Stack className="navbar-content" h="100%" justify="space-between" gap="md">
            <Stack gap="md">
              <Box className="navbar-brand">
                <Text fw={900} size="lg">Command Center</Text>
                <Badge color="dark" radius="sm" size="xs" variant="light">v0.1.0</Badge>
              </Box>
              <Button className="new-issue-button" leftSection={<IconPlus size={15} />} variant="filled">
                New Issue
              </Button>
              <Stack gap={4}>
                <NavItem
                  active={activeTab === "issues"}
                  icon={<IconListDetails size={18} />}
                  label="Issues"
                  onClick={() => setActiveTab("issues")}
                />
                <NavItem
                  active={activeTab === "workspaces"}
                  icon={<IconBriefcase size={18} />}
                  label="Workspaces"
                  onClick={() => setActiveTab("workspaces")}
                />
                <NavItem
                  active={activeTab === "prs"}
                  icon={<IconGitPullRequest size={18} />}
                  label="Pull Requests"
                  onClick={() => setActiveTab("prs")}
                />
              </Stack>
            </Stack>
            <Stack gap={4}>
              <NavItem
                active={activeTab === "settings"}
                icon={<IconAdjustments size={18} />}
                label="Settings"
                onClick={() => setActiveTab("settings")}
              />
              <Anchor className="nav-item" href="https://github.com/emmalabs/linear-codex-orchestrator" target="_blank" rel="noopener noreferrer">
                <Group gap="sm" wrap="nowrap">
                  <IconBook size={18} />
                  <Text fw={700} size="sm">Docs</Text>
                </Group>
                <IconBrandGithub size={14} />
              </Anchor>
            </Stack>
          </Stack>
        </AppShell.Navbar>

        <AppShell.Main>
          <Box className="app-main-inner">
            <Box style={{ display: activeTab === "issues" || activeTab === "prs" ? "block" : "none" }}>
              <DashboardView
                data={data}
                mode={activeTab === "prs" ? "prs" : "issues"}
                onSelectDetail={setSelectedDetail}
                orchestrationRef={orchestrationRef}
                shouldFollowRef={shouldFollowRef}
              />
            </Box>
            <Box style={{ display: activeTab === "workspaces" || activeTab === "settings" ? "block" : "none" }}>
              <SetupView
                configResponse={configResponse}
                section={activeTab === "settings" ? "orchestrator" : "workspaces"}
              />
            </Box>
          </Box>
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

function NavItem(props: {
  active: boolean;
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <UnstyledButton className={`nav-item ${props.active ? "nav-item-active" : ""}`} onClick={props.onClick}>
      <Group gap="sm" wrap="nowrap">
        {props.icon}
        <Text fw={700} size="sm">{props.label}</Text>
      </Group>
    </UnstyledButton>
  );
}
