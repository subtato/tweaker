import { ArrowUpRight, Bot, GitPullRequest, Layers, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { EmptyState, ErrorNotice, PageHeader, Skeleton } from "../components/Ui";
import { useAsync, useDocumentTitle } from "../hooks";
import { timeAgo } from "../lib";
import type { SeerRunSummary } from "../types";

export function Runs() {
  useDocumentTitle("Seer runs");
  const runs = useAsync(() => api.seerRuns({ per_page: 50 }), []);

  return (
    <div className="page">
      <PageHeader
        eyebrow="Seer runs"
        title="Runs driven by Sentry"
        description="Every Autofix step, single-run, and Seer Agent conversation that Sentry dispatched to this service."
        actions={
          <button className="btn btn--ghost" onClick={runs.refresh}>
            <RefreshCw size={15} /> Refresh
          </button>
        }
      />

      {runs.error && <ErrorNotice error={runs.error} onRetry={runs.refresh} />}

      <div className="run-grid">
        {runs.loading && <RunSkeleton />}
        {!runs.loading && runs.data?.items.map((run) => <RunCard run={run} key={run.run_id} />)}
      </div>

      {!runs.loading && !runs.error && !runs.data?.items.length && (
        <EmptyState
          icon={<Bot size={22} />}
          title="No Seer runs yet"
          body="Start Autofix from a Sentry issue, or ask the Seer Agent a question. Runs will appear here as Sentry dispatches them."
          action={
            <Link className="btn btn--primary" to="/settings">
              Review connection settings <ArrowUpRight size={14} />
            </Link>
          }
        />
      )}
    </div>
  );
}

function RunCard({ run }: { run: SeerRunSummary }) {
  const prs = Object.values(run.repo_pr_states ?? {});
  return (
    <Link className={`run-card run-card--${run.status}`} to={`/runs/${run.run_id}`}>
      <div className="run-card__top">
        <span className={`run-status run-status--${run.status}`}>
          <span />
          {run.status}
        </span>
        <span className="run-card__id">#{run.run_id}</span>
      </div>
      <h3>{run.title || "Untitled Seer run"}</h3>
      <div className="run-card__meta">
        <span><Layers size={12} /> {run.blocks} blocks</span>
        <span>{run.feature_id || run.kind}</span>
        <span>{timeAgo(run.updated_at)}</span>
      </div>
      <div className="run-card__foot">
        <span className="mono">org {run.org_id}{run.group_id ? ` · issue ${run.group_id}` : ""}</span>
        {prs.length > 0 && (
          <span className="run-card__pr"><GitPullRequest size={12} /> {prs.length} PR</span>
        )}
      </div>
    </Link>
  );
}

function RunSkeleton() {
  return (
    <>
      {Array.from({ length: 6 }).map((_, index) => (
        <div className="run-card" key={index}>
          <Skeleton className="sk-line sk-line--sm" />
          <Skeleton className="sk-line sk-line--md" />
          <Skeleton className="sk-line" />
        </div>
      ))}
    </>
  );
}
