export function formatBytes(size: number) {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export type StatusTone = "success" | "error" | "active" | "neutral";
export type StatusGroup = "Active" | "Needs attention" | "Ready" | "Done";

export function statusTone(status?: string): StatusTone {
  const normalized = (status || "").toLowerCase();
  if (/\b(failed|blocked|error|failure|cancelled|canceled|stuck)\b/.test(normalized)) {
    return "error";
  }
  if (/\b(implemented|ready|passed|approved|done|complete|completed|merged|success|succeeded)\b/.test(normalized)) {
    return "success";
  }
  if (/\b(implementing|planning|reviewing|fixing|running|active|progress|review|working|queued|started)\b/.test(normalized)) {
    return "active";
  }
  return "neutral";
}

export function statusGroup(status?: string): StatusGroup {
  const tone = statusTone(status);
  if (tone === "error") {
    return "Needs attention";
  }
  if (tone === "success") {
    const normalized = (status || "").toLowerCase();
    return /\b(done|complete|completed|merged)\b/.test(normalized) ? "Done" : "Ready";
  }
  return "Active";
}

export function relativeTime(value?: string | number | Date | null) {
  if (!value) {
    return "";
  }
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  const diffMs = Date.now() - date.getTime();
  const absMs = Math.abs(diffMs);
  const suffix = diffMs >= 0 ? "ago" : "from now";
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (absMs < minute) {
    return "just now";
  }
  if (absMs < hour) {
    return `${Math.round(absMs / minute)}m ${suffix}`;
  }
  if (absMs < day) {
    return `${Math.round(absMs / hour)}h ${suffix}`;
  }
  if (absMs < 7 * day) {
    return `${Math.round(absMs / day)}d ${suffix}`;
  }
  return date.toLocaleDateString();
}

export function formatCount(value?: number | null) {
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
