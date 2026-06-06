import * as React from "react";
import {
  IconAdjustments,
  IconBook,
  IconBrandGithub,
  IconBriefcase,
  IconGitPullRequest,
  IconListDetails
} from "@tabler/icons-react";
import {
  Anchor,
  AppShell,
  Badge,
  Box,
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
import { DetailPage } from "./components/Detail";
import { currentStep } from "./lib/orchestration";

type ActiveSection = "issues" | "workspaces" | "prs" | "settings";

type AppRoute = {
  detail?: {
    key: string;
    kind: SelectedDetail["kind"];
  };
  section: ActiveSection;
};

const appTheme = {
  fontSizes: {
    xs: "14px",
    sm: "16px",
    md: "18px",
    lg: "20px",
    xl: "22px"
  }
};

export function App() {
  const [data, setData] = React.useState<DashboardData>(emptyData);
  const [configResponse, setConfigResponse] = React.useState<ConfigResponse | null>(null);
  const [route, setRoute] = React.useState<AppRoute>(() => routeFromLocation());
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
          archived_issues?: IssueStatus[];
          archived_prs?: PullRequestStatus[];
        };
        if (cancelled) {
          return;
        }
        setData({
          issues: status.issues ?? [],
          prs: status.prs ?? [],
          archivedIssues: status.archived_issues ?? [],
          archivedPrs: status.archived_prs ?? [],
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

  React.useEffect(() => {
    const syncRouteFromHistory = () => {
      setRoute(routeFromLocation());
    };

    window.addEventListener("popstate", syncRouteFromHistory);
    return () => window.removeEventListener("popstate", syncRouteFromHistory);
  }, []);

  const navigate = React.useCallback((nextRoute: AppRoute, options?: { replace?: boolean }) => {
    const nextPath = pathFromRoute(nextRoute);
    const currentPath = `${window.location.pathname}${window.location.search}`;
    const state = {
      commandCenterRoute: true,
      parentPath: nextRoute.detail ? pathFromRoute({ section: nextRoute.section }) : undefined
    };
    if (nextPath !== currentPath) {
      if (options?.replace) {
        window.history.replaceState(state, "", nextPath);
      } else {
        window.history.pushState(state, "", nextPath);
      }
    }
    setRoute(nextRoute);
  }, []);

  const openDetail = React.useCallback((detail: SelectedDetail) => {
    const key = detailKey(detail);
    if (!key) {
      return;
    }
    navigate({
      detail: { kind: detail.kind, key },
      section: detail.kind === "issue" ? "issues" : "prs"
    });
  }, [navigate]);

  const closeDetail = React.useCallback(() => {
    const parentPath = window.history.state?.parentPath;
    if (typeof parentPath === "string" && parentPath === pathFromRoute({ section: route.section })) {
      window.history.back();
      return;
    }
    navigate({ section: route.section });
  }, [navigate, route.section]);

  const archiveDetail = React.useCallback(async (detail: SelectedDetail) => {
    const key = detail.kind === "issue" ? detail.item.identifier : detail.item.key;
    if (!key) {
      throw new Error("This item cannot be archived because it has no stable key.");
    }
    const response = await fetch("/api/status/archive", {
      body: JSON.stringify({ kind: detail.kind, key }),
      headers: { "Content-Type": "application/json" },
      method: "POST"
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({})) as { error?: string };
      throw new Error(payload.error || "Unable to archive item.");
    }
    const payload = await response.json() as {
      status?: {
        issues?: IssueStatus[];
        prs?: PullRequestStatus[];
        archived_issues?: IssueStatus[];
        archived_prs?: PullRequestStatus[];
      };
    };
    setData((current) => ({
      ...current,
      issues: payload.status?.issues ?? current.issues.filter((issue) => issue.identifier !== key),
      prs: payload.status?.prs ?? current.prs.filter((pr) => pr.key !== key),
      archivedIssues: payload.status?.archived_issues ?? current.archivedIssues,
      archivedPrs: payload.status?.archived_prs ?? current.archivedPrs
    }));
    if (route.detail?.kind === detail.kind && route.detail.key === key) {
      navigate({ section: route.section }, { replace: true });
    }
  }, [navigate, route.detail, route.section]);

  const updateDetailStatus = React.useCallback(async (detail: SelectedDetail, status: string) => {
    const key = detail.kind === "issue" ? detail.item.identifier : detail.item.key;
    if (!key) {
      throw new Error("This item cannot be updated because it has no stable key.");
    }
    const response = await fetch("/api/status/update", {
      body: JSON.stringify({ kind: detail.kind, key, status }),
      headers: { "Content-Type": "application/json" },
      method: "POST"
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({})) as { error?: string };
      throw new Error(payload.error || "Unable to update item status.");
    }
    const payload = await response.json() as {
      status?: {
        issues?: IssueStatus[];
        prs?: PullRequestStatus[];
        archived_issues?: IssueStatus[];
        archived_prs?: PullRequestStatus[];
      };
    };
    setData((current) => ({
      ...current,
      issues: payload.status?.issues ?? current.issues.map((issue) => issue.identifier === key ? { ...issue, status } : issue),
      prs: payload.status?.prs ?? current.prs.map((pr) => pr.key === key ? { ...pr, status } : pr),
      archivedIssues: payload.status?.archived_issues ?? current.archivedIssues,
      archivedPrs: payload.status?.archived_prs ?? current.archivedPrs
    }));
  }, []);

  const openSection = React.useCallback((section: ActiveSection) => {
    navigate({ section });
  }, [navigate]);

  const selectedDetail = detailFromRoute(route, data);
  const activeTab = route.section;
  const step = currentStep(data.orchestration);

  return (
    <MantineProvider defaultColorScheme="dark" theme={appTheme}>
      <AppShell
        header={{ height: 56 }}
        navbar={{ width: 248, breakpoint: "sm" }}
        padding="md"
      >
        <AppShell.Header>
          <Group className="app-header-content" h="100%" px="md" wrap="nowrap">
            <Box miw={0}>
              <UnstyledButton className="app-title-link" onClick={() => openSection("issues")}>
                <Title className="app-title" order={1} size="h3">Codex Orchestrator</Title>
              </UnstyledButton>
            </Box>
            <Group className="status-group" gap="xs" wrap="nowrap">
              <Badge color={data.connected ? "green" : "red"} variant="light">
                {data.connected ? "Live" : "Disconnected"}
              </Badge>
              <Text className="header-step" c="dimmed" size="sm">{step.label}</Text>
              <Text c="dimmed" size="sm">{data.refreshedAt.toLocaleTimeString()}</Text>
            </Group>
          </Group>
        </AppShell.Header>

        <AppShell.Navbar className="app-navbar" p="md">
          <Stack className="navbar-content" h="100%" justify="space-between" gap="md">
            <Stack gap="md">
              <Stack gap={4}>
                <NavItem
                  active={activeTab === "issues"}
                  icon={<IconListDetails size={18} />}
                  label="Issues"
                  onClick={() => openSection("issues")}
                />
                <NavItem
                  active={activeTab === "prs"}
                  icon={<IconGitPullRequest size={18} />}
                  label="Pull Requests"
                  onClick={() => openSection("prs")}
                />
              </Stack>
            </Stack>
            <Stack gap={4}>
              <SidebarStatus data={data} />
              <NavItem
                active={activeTab === "workspaces"}
                icon={<IconBriefcase size={18} />}
                label="Workspaces"
                onClick={() => openSection("workspaces")}
              />
              <NavItem
                active={activeTab === "settings"}
                icon={<IconAdjustments size={18} />}
                label="Settings"
                onClick={() => openSection("settings")}
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
            {selectedDetail ? (
              <DetailPage
                detail={selectedDetail}
                onArchive={archiveDetail}
                onBack={closeDetail}
                onStatusChange={updateDetailStatus}
                tasks={data.tasks}
              />
            ) : (
              <>
                <Box style={{ display: activeTab === "issues" || activeTab === "prs" ? "block" : "none" }}>
                  <DashboardView
                    onArchive={archiveDetail}
                    data={data}
                    mode={activeTab === "prs" ? "prs" : "issues"}
                    onSelectDetail={openDetail}
                    orchestrationRef={orchestrationRef}
                    shouldFollowRef={shouldFollowRef}
                    workspaceMap={configResponse?.config.workspace_map}
                  />
                </Box>
                <Box style={{ display: activeTab === "workspaces" || activeTab === "settings" ? "block" : "none" }}>
                  <SetupView
                    configResponse={configResponse}
                    section={activeTab === "settings" ? "orchestrator" : "workspaces"}
                  />
                </Box>
              </>
            )}
          </Box>
        </AppShell.Main>
      </AppShell>
    </MantineProvider>
  );
}

function SidebarStatus(props: { data: DashboardData }) {
  return (
    <Box className="sidebar-status">
      <Group justify="space-between" gap="xs" mb="xs" wrap="nowrap">
        <Text c="dimmed" fw={800} size="xs" tt="uppercase">Status</Text>
        <Badge color={props.data.connected ? "green" : "red"} size="xs" variant="light">
          {props.data.connected ? "Live" : "Offline"}
        </Badge>
      </Group>
      <Group className="sidebar-status-metrics" gap="xs" wrap="nowrap">
        <Box className="sidebar-status-metric">
          <Text c="dimmed" fw={700} size="xs" tt="uppercase">Issues</Text>
          <Text fw={800} size="sm">{props.data.issues.length}</Text>
        </Box>
        <Box className="sidebar-status-metric">
          <Text c="dimmed" fw={700} size="xs" tt="uppercase">Tasks</Text>
          <Text fw={800} size="sm">{props.data.tasks.length}</Text>
        </Box>
      </Group>
      <Text c="dimmed" mt={6} size="xs">Refresh {props.data.refreshedAt.toLocaleTimeString()}</Text>
    </Box>
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

function routeFromLocation(): AppRoute {
  const segments = window.location.pathname.split("/").filter(Boolean);
  const [sectionSegment, ...detailSegments] = segments;
  const detailKey = detailSegments.length ? decodeURIComponent(detailSegments.join("/")) : undefined;
  if (sectionSegment === "pull-requests") {
    return detailKey
      ? { section: "prs", detail: { kind: "pr", key: detailKey } }
      : { section: "prs" };
  }
  if (sectionSegment === "workspaces") {
    return { section: "workspaces" };
  }
  if (sectionSegment === "settings") {
    return { section: "settings" };
  }
  return detailKey
    ? { section: "issues", detail: { kind: "issue", key: detailKey } }
    : { section: "issues" };
}

function pathFromRoute(route: AppRoute) {
  const base = route.section === "prs"
    ? "/pull-requests"
    : route.section === "workspaces"
      ? "/workspaces"
      : route.section === "settings"
        ? "/settings"
        : "/issues";
  return route.detail ? `${base}/${encodeURIComponent(route.detail.key)}` : base;
}

function detailFromRoute(route: AppRoute, data: DashboardData): SelectedDetail | null {
  if (!route.detail) {
    return null;
  }
  if (route.detail.kind === "issue") {
    const issue = [...data.issues, ...data.archivedIssues].find((item) => issueKey(item) === route.detail?.key);
    return issue ? { kind: "issue", item: issue } : null;
  }
  const pr = [...data.prs, ...data.archivedPrs].find((item) => prKey(item) === route.detail?.key);
  return pr ? { kind: "pr", item: pr } : null;
}

function detailKey(detail: SelectedDetail) {
  return detail.kind === "issue" ? issueKey(detail.item) : prKey(detail.item);
}

function issueKey(issue: IssueStatus) {
  return issue.identifier ?? issue.url ?? issue.title;
}

function prKey(pr: PullRequestStatus) {
  return pr.key ?? pr.url ?? pr.title;
}
