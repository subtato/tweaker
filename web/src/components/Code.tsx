import { ChevronDown, ChevronRight, Code2, FileCode2, GitBranch, Plus, Minus } from "lucide-react";
import { useMemo, useState } from "react";
import type { Breadcrumb, Frame, SentryEvent } from "../types";
import { fileBasename, relativePath } from "../lib";
import { CopyButton } from "./Ui";

export function DiffView({ patch }: { patch: string }) {
  const lines = patch.split("\n");
  const stats = useMemo(() => {
    let additions = 0;
    let deletions = 0;
    for (const line of lines) {
      if (line.startsWith("+") && !line.startsWith("+++")) additions += 1;
      if (line.startsWith("-") && !line.startsWith("---")) deletions += 1;
    }
    return { additions, deletions };
  }, [lines]);

  return (
    <div className="diff">
      <div className="diff__bar">
        <span className="diff__stat diff__stat--add">
          <Plus size={12} /> {stats.additions}
        </span>
        <span className="diff__stat diff__stat--del">
          <Minus size={12} /> {stats.deletions}
        </span>
        <span className="diff__spacer" />
        <CopyButton value={patch} label="Copy patch" />
      </div>
      <pre className="diff__code">
        {lines.map((line, index) => {
          const kind = line.startsWith("+++") || line.startsWith("---")
            ? "file"
            : line.startsWith("+")
              ? "add"
              : line.startsWith("-")
                ? "del"
                : line.startsWith("@@")
                  ? "hunk"
                  : "context";
          return (
            <div key={index} className={`diff__line diff__line--${kind}`}>
              <span className="diff__gutter">{index + 1}</span>
              <span className="diff__text">{line || " "}</span>
            </div>
          );
        })}
      </pre>
    </div>
  );
}

export function StackTrace({ event }: { event: SentryEvent }) {
  const [showAll, setShowAll] = useState(false);
  const frames = useMemo(() => {
    const all: Frame[] = [];
    for (const exception of event.exceptions ?? []) {
      all.push(...(exception.stacktrace?.frames ?? []));
    }
    return [...all].reverse();
  }, [event]);
  const appFrames = frames.filter((frame) => frame.inApp);
  const visible = showAll ? frames : (appFrames.length ? appFrames : frames).slice(0, 6);

  if (!frames.length) {
    return <div className="muted-block">No stack frames were captured for this event.</div>;
  }

  return (
    <div className="stack">
      {visible.map((frame, index) => (
        <FrameRow key={`${frame.filename}-${frame.lineNo}-${index}`} frame={frame} index={index} />
      ))}
      {frames.length > visible.length && (
        <button className="stack__more" onClick={() => setShowAll(true)}>
          Show all {frames.length} frames <ChevronDown size={13} />
        </button>
      )}
      {showAll && frames.length > (appFrames.length || 0) && (
        <button className="stack__more" onClick={() => setShowAll(false)}>
          Collapse to application frames <ChevronRight size={13} />
        </button>
      )}
    </div>
  );
}

function FrameRow({ frame, index }: { frame: Frame; index: number }) {
  const [open, setOpen] = useState(index < 2);
  const hasContext = Boolean(frame.contextLine || frame.preContext?.length || frame.postContext?.length);
  return (
    <div className={`frame ${frame.inApp ? "frame--app" : ""}`}>
      <button className="frame__head" onClick={() => hasContext && setOpen(!open)}>
        <span className="frame__index">{String(index + 1).padStart(2, "0")}</span>
        <span className="frame__icon">
          <FileCode2 size={14} />
        </span>
        <span className="frame__body">
          <span className="frame__function">{frame.function || "<anonymous>"}</span>
          <span className="frame__location">
            {relativePath(frame.filename || frame.absPath || "unknown")}
            {frame.lineNo ? `:${frame.lineNo}` : ""}
          </span>
        </span>
        {frame.inApp && <span className="frame__tag">in app</span>}
        {hasContext && (open ? <ChevronDown size={14} /> : <ChevronRight size={14} />)}
      </button>
      {open && hasContext && (
        <pre className="frame__context">
          {(frame.preContext ?? []).map((line, i) => (
            <div key={`pre-${i}`} className="frame__line">
              <span>{frame.lineNo ? frame.lineNo - (frame.preContext?.length ?? 0) + i : ""}</span>
              {line}
            </div>
          ))}
          {frame.contextLine && (
            <div className="frame__line frame__line--active">
              <span>{frame.lineNo}</span>
              {frame.contextLine}
            </div>
          )}
          {(frame.postContext ?? []).map((line, i) => (
            <div key={`post-${i}`} className="frame__line">
              <span>{frame.lineNo ? frame.lineNo + i + 1 : ""}</span>
              {line}
            </div>
          ))}
        </pre>
      )}
    </div>
  );
}

export function Breadcrumbs({ items }: { items: Breadcrumb[] }) {
  if (!items?.length) return <div className="muted-block">No breadcrumbs recorded.</div>;
  return (
    <div className="crumbs">
      {items.map((crumb, index) => (
        <div className="crumb" key={`${crumb.timestamp}-${index}`}>
          <span className="crumb__dot" />
          <span className="crumb__time">
            {crumb.timestamp ? new Date(crumb.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—"}
          </span>
          <span className="crumb__category">{crumb.category || "event"}</span>
          <span className="crumb__message">{crumb.message || "—"}</span>
        </div>
      ))}
    </div>
  );
}

export function EventSummary({ event }: { event: SentryEvent }) {
  const exception = event.exceptions?.[event.exceptions.length - 1];
  return (
    <div className="event-summary">
      <div className="event-summary__icon">
        <Code2 size={18} />
      </div>
      <div>
        <div className="event-summary__type">{exception?.type || "Error"}</div>
        <div className="event-summary__value">{exception?.value || event.message || event.title}</div>
        <div className="event-summary__meta">
          {event.platform && <span>{event.platform}</span>}
          {event.release && <span>release {event.release}</span>}
          {event.environment && <span>{event.environment}</span>}
        </div>
      </div>
    </div>
  );
}

export function FileChips({ files }: { files: string[] }) {
  if (!files?.length) return null;
  return (
    <div className="file-chips">
      {files.map((file) => (
        <span className="file-chip" key={file}>
          <GitBranch size={11} />
          {fileBasename(file)}
        </span>
      ))}
    </div>
  );
}
