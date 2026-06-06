import type { IssueStatus, PullRequestStatus, SelectedDetail, TaskLog } from "../types";

export function latestStatus(items: Array<{ status?: string; updated_at?: string }>) {
  if (!items.length) {
    return "None tracked";
  }
  const latest = items[0];
  return `${latest.status || "Unknown"} - ${latest.updated_at || "no timestamp"}`;
}

export function latestTask(tasks: TaskLog[]) {
  if (!tasks.length) {
    return "No task logs yet";
  }
  return `${tasks[0].title} - ${tasks[0].log_count} log(s)`;
}

export function currentLiveActivity(tasks: TaskLog[]) {
  const running = tasks
    .flatMap((task) => task.stages.map((stage) => ({ task, stage })))
    .filter((item) => item.stage.summary?.status === "running")
    .sort((a, b) => b.stage.modified - a.stage.modified);
  return running[0] ?? null;
}

export function issueRepos(issue: IssueStatus) {
  return Array.isArray(issue.repos) ? issue.repos : [];
}

export function legacyChangedRepos(issue: IssueStatus) {
  return typeof issue.repos === "string" ? issue.repos : undefined;
}

export function issuePullRequests(issue: IssueStatus) {
  if (!issue.prs) {
    return [];
  }
  return issue.prs
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function prLabel(url: string) {
  const match = url.match(/github\.com\/([^/]+\/[^/]+)\/pull\/(\d+)/);
  return match ? `${match[1]}#${match[2]}` : url;
}

export function tasksForDetail(detail: SelectedDetail, tasks: TaskLog[]) {
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
