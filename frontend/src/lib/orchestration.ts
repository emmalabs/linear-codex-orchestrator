export function logLineKind(line: string) {
  if (/failed|blocked|error|unauthorized|auth required/i.test(line)) {
    return "log-line-error";
  }
  if (/found [1-9]\d* new feedback|feedback addressed|PR ready|review passed|completed|complete$/i.test(line)) {
    return "log-line-success";
  }
  if (/started|processing|resuming|implementation|optimization|review|fixing|creating|pushing|committing/i.test(line)) {
    return "log-line-active";
  }
  if (/Tick complete|sleeping/i.test(line)) {
    return "log-line-idle";
  }
  if (/checking open PRs|Polling Linear|found \d+ .*issue|found \d+ open PR|no new PR feedback|sleeping/i.test(line)) {
    return "log-line-muted";
  }
  return "";
}

export function currentStep(orchestration: string) {
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

export function stageName(name: string) {
  const stem = name.replace(/\.log$/, "");
  const body = stem.replace(/^\d{8}-\d{6}-/, "");
  return body
    .split("-")
    .map((part) => part ? part[0].toUpperCase() + part.slice(1) : part)
    .join(" ");
}
