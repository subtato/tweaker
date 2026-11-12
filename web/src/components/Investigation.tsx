import {
  AlertTriangle,
  ArrowUpRight,
  Check,
  ChevronDown,
  Circle,
  Download,
  GitPullRequest,
  Loader2,
  RotateCcw,
  Sparkles,
  Terminal,
  XCircle,
} from "lucide-react";
import { useState } from "react";
import type { IssueSummary, RunDetail, RunStep } from "../types";
import { confidencePct, formatDuration, scoreTone } from "../lib";
import { DiffView, FileChips } from "./Code";
import { Confidence, StatusBadge } from "./Badges";
import { CopyButton, RichText } from "./Ui";

export function RunTimeline({ steps }: { steps: RunStep[] }) {
  if (!steps.length) return null;
  return (
    <div className="timeline">
      {steps.map((step, index) => (
        <div className={`tl-step tl-step--${step.status}`} key={step.id}>
          <div className="tl-step__rail">
            <StepIcon status={step.status} />
            {index < steps.length - 1 && <span className="tl-step__line" />}
          </div>
          <div className="tl-step__body">
            <div className="tl-step__head">
              <span className="tl-step__title">{step.title}</span>
              {step.status === "warning" && <span className="tl-step__tag">warning</span>}
            </div>
            {step.body && <p className="tl-step__text">{step.body}</p>}
            {Array.isArray(step.data?.files) && (
              <FileChips files={step.data.files as string[]} />
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function StepIcon({ status }: { status: RunStep["status"] }) {
  if (status === "running") {
    return (
      <span className="tl-icon tl-icon--running">
        <Loader2 size={13} className="spin" />
      </span>
    );
  }
  if (status === "succeeded") {
    return (
      <span className="tl-icon tl-icon--success">
        <Check size={12} />
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="tl-icon tl-icon--danger">
        <XCircle size={12} />
      </span>
    );
  }
  if (status === "warning") {
    return (
      <span className="tl-icon tl-icon--warning">
        <AlertTriangle size={12} />
      </span>
    );
  }
  return (
    <span className="tl-icon tl-icon--muted">
      <Circle size={10} />
    </span>
  );
}

export function RunResult({
  run,
  issue,
  onPublish,
  onRetry,
  onDownload,
  publishing = false,
}: {
  run: RunDetail;
  issue?: IssueSummary | null;
  onPublish?: () => void;
  onRetry?: () => void;
  onDownload?: () => void;
  publishing?: boolean;
}) {
  const [showPatch, setShowPatch] = useState(false);
  const evidence = run.run_metadata?.evidence ?? [];
  const fixPlan = run.run_metadata?.fix_plan ?? [];
  const tests = run.run_metadata?.tests ?? [];
  const active = run.status === "queued" || run.status === "running";

  return (
    <div className="investigation">
      <div className="investigation__head">
        <div>
          <div className="eyebrow">Investigation</div>
          <div className="investigation__statusline">
            <StatusBadge status={run.status} />
            <span className="muted">
              {run.kind === "autofix" ? "Autofix" : "Root cause"} · {run.provider}/{run.model || "local"}
            </span>
          </div>
        </div>
        <div className="investigation__meta">
          {run.finished_at && <span>{formatDuration(run.started_at, run.finished_at)}</span>}
          <Confidence value={run.confidence} />
        </div>
      </div>

      <RunTimeline steps={run.steps} />

      {active && (
        <div className="running-note">
          <Loader2 size={14} className="spin" />
          <span>Tweaker is working through the evidence. This view updates live.</span>
        </div>
      )}

      {run.error && (
        <div className="notice notice--error">
          <div>
            <strong>Investigation failed</strong>
            <span>{run.error}</span>
          </div>
        </div>
      )}

      {run.summary && (
        <section className="result-block">
          <h3>
            <Sparkles size={15} /> What happened
          </h3>
          <RichText text={run.summary} />
        </section>
      )}

      {run.root_cause && (
        <section className="result-block result-block--root">
          <h3>
            <Terminal size={15} /> Root cause
          </h3>
          <RichText text={run.root_cause} />
        </section>
      )}

      {evidence.length > 0 && (
        <section className="result-block">
          <h3>Evidence</h3>
          <div className="evidence">
            {evidence.map((item, index) => (
              <div className="evidence__item" key={index}>
                <span className="evidence__marker">{index + 1}</span>
                <div>
                  <div className="evidence__where">
                    {item.file ? (
                      <code>
                        {item.file}
                        {item.line ? `:${item.line}` : ""}
                      </code>
                    ) : (
                      <span>Telemetry</span>
                    )}
                  </div>
                  <p>{item.explanation}</p>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {fixPlan.length > 0 && (
        <section className="result-block">
          <h3>Fix plan</h3>
          <ol className="plan">
            {fixPlan.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ol>
          {tests.length > 0 && (
            <div className="tests">
              <span>Validate with</span>
              {tests.map((test) => (
                <code key={test}>{test}</code>
              ))}
            </div>
          )}
        </section>
      )}

      {run.run_metadata?.risk && (
        <div className={`risk risk--${run.run_metadata.risk}`}>
          <AlertTriangle size={14} />
          <span>
            <strong>{String(run.run_metadata.risk)} risk</strong>
            {run.run_metadata.needs_human
              ? " · human review recommended before shipping"
              : " · no human blockers detected"}
          </span>
        </div>
      )}

      {run.patch && (
        <section className="result-block result-block--patch">
          <div className="patch-head">
            <h3>
              <GitPullRequest size={15} /> Proposed patch
            </h3>
            <div className="patch-actions">
              <button className="btn btn--small btn--ghost" onClick={() => setShowPatch(!showPatch)}>
                {showPatch ? "Hide diff" : "Show diff"}
                <ChevronDown size={13} className={showPatch ? "flip" : ""} />
              </button>
              {onDownload && (
                <button className="btn btn--small btn--ghost" onClick={onDownload}>
                  <Download size={13} /> Download
                </button>
              )}
              {onPublish && !run.pr_url && (
                <button className="btn btn--small btn--primary" onClick={onPublish} disabled={publishing}>
                  {publishing ? <Loader2 size={13} className="spin" /> : <GitPullRequest size={13} />}
                  {publishing ? "Opening…" : "Open pull request"}
                </button>
              )}
              {run.pr_url && (
                <a className="btn btn--small btn--primary" href={run.pr_url} target="_blank" rel="noreferrer">
                  <ArrowUpRight size={13} /> View PR
                </a>
              )}
            </div>
          </div>
          {showPatch && <DiffView patch={run.patch} />}
          {!showPatch && (
            <div className="patch-summary">
              <span>
                Patch ready · {run.patch.split("\n").filter((line) => line.startsWith("+") && !line.startsWith("+++")).length} additions
              </span>
              <CopyButton value={run.patch} label="Copy patch" />
            </div>
          )}
          {run.run_metadata?.patch_error && (
            <div className="notice notice--warning">Patch validation: {String(run.run_metadata.patch_error)}</div>
          )}
          {run.run_metadata?.publish_error && (
            <div className="notice notice--warning">Publish: {String(run.run_metadata.publish_error)}</div>
          )}
        </section>
      )}

      {!run.patch && run.status === "succeeded" && (
        <div className="notice notice--muted">
          No code patch was proposed. The investigation still produced a root cause and fix plan.
        </div>
      )}

      {run.status === "failed" && onRetry && (
        <button className="btn btn--ghost" onClick={onRetry}>
          <RotateCcw size={14} /> Retry investigation
        </button>
      )}

      {issue && (
        <div className="investigation__footer">
          <span>
            {issue.short_id} · {issue.event_count.toLocaleString()} events · fixability{" "}
            <span className={`score-text score-text--${scoreTone(issue.fixability_score)}`}>
              {issue.fixability_score ?? "—"}
            </span>
          </span>
          {run.confidence !== null && run.confidence !== undefined && (
            <span>{confidencePct(run.confidence)} confidence</span>
          )}
        </div>
      )}
    </div>
  );
}
