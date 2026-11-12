import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  Loader2,
  MinusCircle,
  OctagonAlert,
  Radio,
  XCircle,
} from "lucide-react";
import { levelTone, scoreTone } from "../lib";
import type { Level, RunStatus } from "../types";

const STATUS_ICON = {
  succeeded: CheckCircle2,
  running: Loader2,
  queued: CircleDashed,
  failed: XCircle,
  cancelled: MinusCircle,
};

export function StatusBadge({ status, label }: { status: RunStatus | string; label?: string }) {
  const Icon = STATUS_ICON[status as keyof typeof STATUS_ICON] ?? Radio;
  const text =
    label ??
    ({
      succeeded: "Succeeded",
      running: "Running",
      queued: "Queued",
      failed: "Failed",
      cancelled: "Cancelled",
    }[status] ??
      status);
  return (
    <span className={`status status--${status}`}>
      <Icon size={12} className={status === "running" ? "spin" : undefined} />
      {text}
    </span>
  );
}

export function LevelBadge({ level }: { level: Level }) {
  const tone = levelTone(level);
  const Icon = tone === "fatal" ? OctagonAlert : tone === "warning" ? AlertTriangle : tone === "info" ? Radio : XCircle;
  return (
    <span className={`level level--${tone}`} title={`${level} level`}>
      <Icon size={12} />
      {String(level || "error")}
    </span>
  );
}

export function FixabilityMeter({
  score,
  compact = false,
}: {
  score?: number | null;
  compact?: boolean;
}) {
  const tone = scoreTone(score);
  const value = score ?? 0;
  const label =
    score === null || score === undefined
      ? "Not scanned"
      : tone === "high"
        ? "High"
        : tone === "medium"
          ? "Medium"
          : "Low";
  return (
    <div className={`fixability fixability--${tone} ${compact ? "fixability--compact" : ""}`}>
      <div className="fixability__head">
        <span className="fixability__label">Fixability</span>
        <span className="fixability__value">
          {score === null || score === undefined ? "—" : value}
          {score !== null && score !== undefined && <small>/100</small>}
        </span>
      </div>
      <div
        className="meter"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={score ?? 0}
        aria-label={`Fixability ${label}`}
      >
        <span className="meter__fill" style={{ width: `${Math.max(value, score ? 3 : 0)}%` }} />
      </div>
      {!compact && (
        <div className="fixability__foot">
          <span>{label}</span>
          <span>code fix likelihood</span>
        </div>
      )}
    </div>
  );
}

export function Confidence({ value }: { value?: number | null }) {
  if (value === null || value === undefined) return null;
  return (
    <span className="confidence">
      <span className="confidence__dot" style={{ opacity: 0.45 + value * 0.55 }} />
      {Math.round(value * 100)}% confidence
    </span>
  );
}
