export function formatBytes(size: number) {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
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
