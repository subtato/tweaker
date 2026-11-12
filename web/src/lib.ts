import type { Level, RunStatus } from "./types";

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}m`;
  if (value >= 10_000) return `${(value / 1_000).toFixed(0)}k`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

export function timeAgo(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  const abs = Math.abs(seconds);
  const formatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  if (abs < 60) return formatter.format(-seconds, "second");
  if (abs < 3600) return formatter.format(-Math.round(seconds / 60), "minute");
  if (abs < 86400) return formatter.format(-Math.round(seconds / 3600), "hour");
  if (abs < 86400 * 30) return formatter.format(-Math.round(seconds / 86400), "day");
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatDuration(start?: string | null, end?: string | null): string {
  if (!start || !end) return "—";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (Number.isNaN(ms) || ms < 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms / 60_000)}m`;
}

export function levelTone(level: Level | string): "fatal" | "error" | "warning" | "info" | "debug" {
  const value = String(level).toLowerCase();
  if (value === "fatal" || value === "critical") return "fatal";
  if (value === "warning") return "warning";
  if (value === "info" || value === "debug") return "info";
  return "error";
}

export function statusTone(status: RunStatus | string): string {
  switch (status) {
    case "succeeded":
      return "success";
    case "running":
      return "running";
    case "queued":
      return "queued";
    case "failed":
      return "danger";
    case "cancelled":
      return "muted";
    default:
      return "muted";
  }
}

export function scoreTone(score?: number | null): "high" | "medium" | "low" | "none" {
  if (score === null || score === undefined) return "none";
  if (score >= 75) return "high";
  if (score >= 50) return "medium";
  return "low";
}

export function confidencePct(value?: number | null): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)}%`;
}

export function truncate(value: string, length: number): string {
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

export function fileBasename(path: string): string {
  return path.split("/").filter(Boolean).pop() ?? path;
}

export function humanizeAction(action: string): string {
  return action
    .replace(/[._]/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export function relativePath(path: string): string {
  return path.replace(/^webpack:\/\//, "").replace(/^file:\/\//, "");
}
