import {
  ArrowUpRight,
  Code2,
  Database,
  GitPullRequest,
  Loader2,
  RefreshCw,
  ScanSearch,
  Sparkles,
  Tag,
  Terminal,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { LevelBadge, StatusBadge } from "../components/Badges";
import { Breadcrumbs, EventSummary, StackTrace } from "../components/Code";
import { RunResult } from "../components/Investigation";
import { EmptyState, ErrorNotice, PageHeader, RichText, Skeleton } from "../components/Ui";
import { useAsync, useDocumentTitle, usePolling } from "../hooks";
import { formatNumber, timeAgo } from "../lib";
import { useOrg } from "../OrgContext";
import type { AppConfig, IssueDetail, RunDetail } from "../types";
import { useOutletContext } from "react-router-dom";

type Tab = "investigation" | "stack" | "breadcrumbs" | "tags";

export function IssuePage() {
  const { issueId = "" } = useParams();
  const { config } = useOutletContext<{ config: AppConfig }>();
  const { orgId } = useOrg();
  const issue = useAsync(() => api.issue(issueId), [issueId]);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [runLoading, setRunLoading] = useState(false);
  const [tab, setTab] = useState<Tab>("investigation");
  const [mode, setMode] = useState<"investigate" | "autofix">("investigate");
  const [publish, setPublish] = useState(false);
  const [starting, setStarting] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  useDocumentTitle(issue.data?.short_id ?? "Issue");

  const latestRunId = issue.data?.latest_run_id ?? null;
  const active = run?.status === "queued" || run?.status === "running";

  useEffect(() => {
    if (!latestRunId) {
      setRun(null);
      return;
    }
    let cancelled = false;
    setRunLoading(true);
    api
      .run(latestRunId)
      .then((value) => {
        if (!cancelled) setRun(value);
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setRunLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [latestRunId]);

  usePolling(() => {
    if (!run) return;
    api
      .run(run.id)
      .then((value) => {
        setRun(value);
        if (value.status !== "queued" && value.status !== "running") {
          issue.refresh().catch(() => undefined);
        }
      })
      .catch(() => undefined);
  }, Boolean(active), 1500);

  async function start() {
    setStarting(true);
    setActionError(null);
    try {
      const created = await api.investigate(issueId, {
        mode,
        publish: mode === "autofix" && publish,
      });
      const detail = await api.run(created.id);
      setRun(detail);
      await issue.refresh();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not start the run");
    } finally {
      setStarting(false);
    }
  }

  async function scan() {
    setActionError(null);
    try {
      await api.scan(issueId);
      await issue.refresh();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not scan the issue");
    }
  }

  async function refresh() {
    setActionError(null);
    try {
      await api.refreshIssue(issueId);
      await issue.refresh();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not refresh the issue");
    }
  }

  async function publishRun() {
    if (!run) return;
    setPublishing(true);
    setActionError(null);
    try {
      await api.publishRun(run.id, {});
      setRun(await api.run(run.id));
      await issue.refresh();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not publish the patch");
    } finally {
      setPublishing(false);
    }
  }

  async function retry() {
    if (!run) return;
    try {
      const created = await api.retryRun(run.id);
      setRun(await api.run(created.id));
      await issue.refresh();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not retry");
    }
  }

  if (issue.error) {
    return <ErrorNotice error={issue.error} onRetry={issue.refresh} />;
  }

  if (!issue.data) {
    return (
      <div className="page">
        <Skeleton className="sk-title" />
        <Skeleton className="sk-block" />
      </div>
    );
  }

  const detail: IssueDetail = issue.data;
  const event = detail.latest_event;

  return (
    <div className="page issue-detail-page">
      <div className="issue-hero">
        <div className="issue-hero__top">
          <Link className="back-link" to="/issues">← Sentry mirror</Link>
          <div className="issue-hero__actions">
            <button className="btn btn--small btn--ghost" onClick={refresh}>
              <RefreshCw size={13} /> Refresh
            </button>
            <button className="btn btn--small btn--ghost" onClick={scan}>
              <ScanSearch size={13} /> Scan fixability
            </button>
            <a className="btn btn--small btn--ghost" href={detail.permalink} target="_blank" rel="noreferrer">
              Open in Sentry <ArrowUpRight size={13} />
            </a>
          </div>
        </div>
        <div className="issue-hero__main">
          <div className="issue-hero__identity">
            <div className="issue-hero__badges">
              <LevelBadge level={detail.level} />
              <span className="mono issue-hero__short">{detail.short_id}</span>
              <span className="status status--queued">{detail.status}</span>
            </div>
            <h1>{detail.title}</h1>
            <p className="mono issue-hero__culprit">{detail.culprit || detail.project_slug}</p>
          </div>
          <div className="issue-hero__numbers">
            <div><strong>{formatNumber(detail.event_count)}</strong><span>events</span></div>
            <div><strong>{formatNumber(detail.user_count)}</strong><span>users</span></div>
            <div><strong>{detail.fixability_score ?? "—"}</strong><span>fixability</span></div>
            <div><strong>{timeAgo(detail.last_seen)}</strong><span>last seen</span></div>
          </div>
        </div>
        <div className="issue-hero__chips">
          <span><Database size={12} /> {detail.project_slug}</span>
          <span><Code2 size={12} /> {detail.platform || "unknown platform"}</span>
          {detail.issue_metadata?.type ? <span><Tag size={12} /> {String(detail.issue_metadata.type)}</span> : null}
          {detail.repo_id ? <span><GitPullRequest size={12} /> repository mapped</span> : null}
        </div>
      </div>

      {actionError && <ErrorNotice error={actionError} />}

      <div className="issue-layout">
        <div className="issue-main">
          <div className="card event-card">
            <EventSummary event={event} />
            <div className="tabs tabs--card">
              {[
                ["investigation", "Investigation"],
                ["stack", "Stack trace"],
                ["breadcrumbs", "Breadcrumbs"],
                ["tags", "Tags"],
              ].map(([value, label]) => (
                <button
                  key={value}
                  className={`tab ${tab === value ? "tab--active" : ""}`}
                  onClick={() => setTab(value as Tab)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="event-card__body">
              {tab === "investigation" && (
                <div className="event-overview">
                  <div className="event-overview__section">
                    <h3><Terminal size={14} /> Latest exception</h3>
                    <StackTrace event={event} />
                  </div>
                  {detail.fixability_reason && (
                    <div className="event-overview__section">
                      <h3><Sparkles size={14} /> Fixability signal</h3>
                      <RichText text={detail.fixability_reason} />
                      <div className="signal-chips">
                        {(detail.fixability_signals ?? []).map((signal) => (
                          <span key={signal}>{signal}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
              {tab === "stack" && <StackTrace event={event} />}
              {tab === "breadcrumbs" && <Breadcrumbs items={event.breadcrumbs ?? []} />}
              {tab === "tags" && (
                <div className="tag-table">
                  {Object.entries(event.tags ?? {}).map(([key, value]) => (
                    <div className="tag-row" key={key}>
                      <span>{key}</span>
                      <code>{value}</code>
                    </div>
                  ))}
                  {!Object.keys(event.tags ?? {}).length && <div className="muted-block">No tags recorded.</div>}
                </div>
              )}
            </div>
          </div>
        </div>

        <aside className="issue-side">
          {runLoading && !run && (
            <div className="card investigation-card">
              <Skeleton className="sk-line sk-line--md" />
              <Skeleton className="sk-block" />
            </div>
          )}
          {!run && !runLoading && (
            <div className="card investigation-card investigation-card--empty">
              <div className="investigation-empty__icon"><Sparkles size={20} /></div>
              <div className="eyebrow">Local workflow</div>
              <h2>Investigate this issue</h2>
              <p>
                This optional local workflow calls the same model and code-context pipeline as the
                Seer service. Sentry's Autofix drawer uses the Seer contract directly.
              </p>
              <div className="segmented">
                <button className={mode === "investigate" ? "segmented__item segmented__item--active" : "segmented__item"} onClick={() => setMode("investigate")}>
                  Root cause
                </button>
                <button className={mode === "autofix" ? "segmented__item segmented__item--active" : "segmented__item"} onClick={() => setMode("autofix")}>
                  Autofix
                </button>
              </div>
              {mode === "autofix" && config.publishing_enabled && (
                <label className="check-row">
                  <input type="checkbox" checked={publish} onChange={(event) => setPublish(event.target.checked)} />
                  <span>Open a pull request after validating the patch</span>
                </label>
              )}
              <button className="btn btn--primary btn--wide" onClick={start} disabled={starting}>
                {starting ? <Loader2 size={15} className="spin" /> : <Sparkles size={15} />}
                {starting ? "Starting…" : mode === "autofix" ? "Start autofix" : "Start investigation"}
              </button>
            </div>
          )}
          {run && (
            <div className="card investigation-card">
              <RunResult
                run={run}
                issue={detail}
                onPublish={run.patch && !run.pr_url ? publishRun : undefined}
                onRetry={retry}
                onDownload={() => api.downloadPatch(run.id)}
                publishing={publishing}
              />
            </div>
          )}
          {!run && detail.runs.length > 0 && (
            <div className="card">
              <h3 className="card-title">Run history</h3>
              {detail.runs.map((item) => (
                <div className="history-row" key={item.id}>
                  <StatusBadge status={item.status} />
                  <span>{item.kind}</span>
                  <span>{timeAgo(item.created_at)}</span>
                </div>
              ))}
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
