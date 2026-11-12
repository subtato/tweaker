import { ArrowUpRight, Filter, RefreshCw, Search, SlidersHorizontal } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { FixabilityMeter, LevelBadge, StatusBadge } from "../components/Badges";
import { EmptyState, ErrorNotice, PageHeader, Skeleton } from "../components/Ui";
import { useAsync, useDocumentTitle } from "../hooks";
import { formatNumber, timeAgo } from "../lib";
import { useOrg } from "../OrgContext";
import type { IssueSummary } from "../types";

const TABS = [
  { value: "unresolved", label: "Unresolved" },
  { value: "all", label: "All" },
  { value: "resolved", label: "Resolved" },
  { value: "ignored", label: "Ignored" },
];

export function Issues() {
  useDocumentTitle("Sentry mirror");
  const { org, orgId } = useOrg();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("search") ?? "");
  const [status, setStatus] = useState(params.get("status") ?? "unresolved");
  const [level, setLevel] = useState(params.get("level") ?? "");
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);

  useEffect(() => {
    setSearch(params.get("search") ?? "");
  }, [params]);

  const issues = useAsync(
    () =>
      api.issues({
        org_id: orgId,
        status,
        level: level || undefined,
        search: search || undefined,
        per_page: 50,
      }),
    [orgId, status, level, search],
  );

  function submit(event: FormEvent) {
    event.preventDefault();
    const next = new URLSearchParams(params);
    if (search) next.set("search", search);
    else next.delete("search");
    next.set("status", status);
    if (level) next.set("level", level);
    else next.delete("level");
    setParams(next);
  }

  async function sync() {
    if (!org) return;
    setSyncing(true);
    setSyncMessage(null);
    try {
      const result = await api.syncOrg(org.id);
      setSyncMessage(`Synced ${result.synced} issue${result.synced === 1 ? "" : "s"} from Sentry.`);
      await issues.refresh();
    } catch (error) {
      setSyncMessage(error instanceof Error ? error.message : "Sync failed");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <div className="page">
      <PageHeader
        eyebrow="Sentry mirror"
        title="Issues cached for local workflows"
        description="Sentry remains the source of truth. This mirror powers the optional local investigation workflow and is useful for debugging the Seer connection."
        actions={
          <>
            <button className="btn btn--ghost" onClick={sync} disabled={syncing}>
              <RefreshCw size={15} className={syncing ? "spin" : undefined} />
              {syncing ? "Syncing…" : "Sync from Sentry"}
            </button>
            <a className="btn btn--primary" href={org?.sentry_url ?? "#"} target="_blank" rel="noreferrer">
              Open Sentry <ArrowUpRight size={15} />
            </a>
          </>
        }
      />

      {syncMessage && <div className="notice notice--muted">{syncMessage}</div>}

      <div className="filterbar">
        <div className="tabs">
          {TABS.map((tab) => (
            <button
              key={tab.value}
              className={`tab ${status === tab.value ? "tab--active" : ""}`}
              onClick={() => {
                setStatus(tab.value);
                const next = new URLSearchParams(params);
                next.set("status", tab.value);
                setParams(next);
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <form className="filterbar__right" onSubmit={submit}>
          <label className="select-wrap">
            <Filter size={13} />
            <select value={level} onChange={(event) => setLevel(event.target.value)} aria-label="Filter by level">
              <option value="">All levels</option>
              <option value="fatal">Fatal</option>
              <option value="error">Error</option>
              <option value="warning">Warning</option>
              <option value="info">Info</option>
            </select>
          </label>
          <label className="search search--compact">
            <Search size={14} />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search title, culprit, short ID…"
            />
          </label>
          <button className="icon-btn" type="submit" aria-label="Apply filters">
            <SlidersHorizontal size={15} />
          </button>
        </form>
      </div>

      {issues.error && <ErrorNotice error={issues.error} onRetry={issues.refresh} />}

      <div className="card issue-table">
        <div className="issue-table__head">
          <span>Issue</span>
          <span>Events</span>
          <span>Fixability</span>
          <span>Last seen</span>
          <span />
        </div>
        {issues.loading && <IssueSkeleton />}
        {!issues.loading &&
          issues.data?.items.map((issue) => <IssueRow issue={issue} key={issue.id} />)}
      </div>

      {!issues.loading && !issues.error && !issues.data?.items.length && (
        <EmptyState
          title="No issues in the mirror"
          body="Sync from Sentry or broaden the filters. Sentry's own issue page is still the primary Seer surface."
          action={
            <button className="btn btn--primary" onClick={sync} disabled={syncing}>
              <RefreshCw size={14} /> Sync from Sentry
            </button>
          }
        />
      )}
    </div>
  );
}

function IssueRow({ issue }: { issue: IssueSummary }) {
  return (
    <Link className="issue-table__row" to={`/issues/${issue.id}`}>
      <div className="issue-table__issue">
        <span className={`issue-dot issue-dot--${issue.level}`} />
        <div>
          <div className="issue-table__title">{issue.title}</div>
          <div className="issue-table__meta">
            <span className="mono">{issue.short_id}</span>
            <span>{issue.culprit || issue.project_slug}</span>
            <LevelBadge level={issue.level} />
          </div>
        </div>
      </div>
      <div className="issue-table__number">
        <strong>{formatNumber(issue.event_count)}</strong>
        <span>{formatNumber(issue.user_count)} users</span>
      </div>
      <div className="issue-table__fix">
        <FixabilityMeter score={issue.fixability_score} compact />
        {issue.latest_run_status && <StatusBadge status={issue.latest_run_status} />}
      </div>
      <div className="issue-table__time">{timeAgo(issue.last_seen)}</div>
      <div className="issue-table__action">
        <ArrowUpRight size={15} />
      </div>
    </Link>
  );
}

function IssueSkeleton() {
  return (
    <div className="issue-table__skeleton">
      {Array.from({ length: 6 }).map((_, index) => (
        <div className="issue-table__skeleton-row" key={index}>
          <Skeleton className="sk-line" />
          <Skeleton className="sk-line sk-line--sm" />
          <Skeleton className="sk-line sk-line--sm" />
        </div>
      ))}
    </div>
  );
}
