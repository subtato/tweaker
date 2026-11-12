import {
  ArrowRight,
  ArrowUpRight,
  Bot,
  CheckCircle2,
  ChevronRight,
  GitPullRequest,
  ShieldCheck,
  TriangleAlert,
  Users,
  Zap,
} from "lucide-react";
import { Link, useNavigate, useOutletContext } from "react-router-dom";
import { api } from "../api";
import { FixabilityMeter, LevelBadge, StatusBadge } from "../components/Badges";
import { PageHeader, SectionHead, Skeleton, StatTile } from "../components/Ui";
import { useAsync } from "../hooks";
import { formatNumber, timeAgo } from "../lib";
import { useOrg } from "../OrgContext";
import type { AppConfig, Stats } from "../types";

export function Overview() {
  const { org, orgId } = useOrg();
  const { config, stats } = useOutletContext<{ stats: Stats | null; config: AppConfig }>();
  const navigate = useNavigate();
  const issues = useAsync(
    () => api.issues({ org_id: orgId, status: "unresolved", per_page: 6 }),
    [orgId],
  );
  const runs = useAsync(() => api.runs({ org_id: orgId, per_page: 5 }), [orgId]);

  const latestIssue = issues.data?.items[0];
  const activeRun = runs.data?.items.find((run) => run.status === "running" || run.status === "queued");
  const latestRun = runs.data?.items[0];

  return (
    <div className="page">
      <PageHeader
        eyebrow={`${org?.name ?? "Workspace"} · ${config.demo_mode ? "Demo data" : "Seer service online"}`}
        title={
          <>
            The Seer service
            <br />
            <span className="title-gradient">for self-hosted Sentry.</span>
          </>
        }
        description="Sentry's existing Seer UI drives Autofix, issue summaries, and the Seer Agent through this service. This console is for operators: contract health, recent runs, and guardrails."
        actions={
          <>
            <a className="btn btn--ghost" href={org?.sentry_url ?? "#"} target="_blank" rel="noreferrer">
              Open Sentry <ArrowUpRight size={15} />
            </a>
            <Link className="btn btn--primary" to="/runs">
              View Seer runs <ArrowRight size={15} />
            </Link>
          </>
        }
      />

      <div className="hero">
        <div className="hero__grid" />
        <div className="hero__content">
          <div className="hero__kicker">
            <span className={`pulse-dot ${activeRun ? "pulse-dot--live" : ""}`} />
            {activeRun ? "Seer run in progress" : "Seer contract live"}
            <span className="hero__kicker-sep" />
            <span>{config.ai_provider === "demo" ? "Local analyzer" : config.ai_model}</span>
          </div>
          <h2>
            {activeRun
              ? "Sentry is driving a live Autofix run."
              : "Sentry drives the workflow. Tweaker supplies the service."}
          </h2>
          <p>
            {activeRun
              ? "The run is collecting Sentry context, locating source, and validating a fix. Follow the block timeline in Runs."
              : "Point SEER_AUTOFIX_URL and SEER_SUMMARIZATION_URL at Tweaker, share the Seer secrets, and use Sentry's own issue page. No separate Seer clone required."}
          </p>
          <div className="hero__actions">
            {activeRun ? (
              <Link className="btn btn--primary" to={`/runs/${activeRun.id}`}>
                Follow Seer run <ArrowRight size={15} />
              </Link>
            ) : (
              <a className="btn btn--primary" href={org?.sentry_url ?? "#"} target="_blank" rel="noreferrer">
                Open Sentry Seer <ArrowUpRight size={15} />
              </a>
            )}
            <Link className="btn btn--ghost" to="/runs">
              View recent runs
            </Link>
          </div>
        </div>
        <div className="hero__preview">
          <div className="mini-run">
            <div className="mini-run__head">
              <span className="mini-run__dot" />
              <span>{activeRun ? "Seer run" : latestRun ? "Latest Seer run" : "Seer service ready"}</span>
              <span className="mini-run__id">
                {activeRun?.id.slice(0, 8) ?? latestRun?.id.slice(0, 8) ?? "—"}
              </span>
            </div>
            <div className="mini-run__issue">
              {latestIssue?.short_id ?? "BACKEND-1A2B"}
              <span>{latestIssue?.title ?? "Waiting for an issue"}</span>
            </div>
            <div className="mini-steps">
              {[
                ["Collect Sentry context", "done"],
                ["Locate source", "done"],
                ["Root cause analysis", activeRun ? "live" : "done"],
                ["Validate patch", activeRun ? "wait" : latestRun?.status === "succeeded" ? "done" : "wait"],
              ].map(([label, state]) => (
                <div className={`mini-step mini-step--${state}`} key={label}>
                  <span className="mini-step__icon">
                    {state === "done" ? <CheckCircle2 size={12} /> : state === "live" ? <span className="mini-pulse" /> : <span />}
                  </span>
                  <span>{label}</span>
                  <span className="mini-step__state">{state === "done" ? "complete" : state === "live" ? "running" : "queued"}</span>
                </div>
              ))}
            </div>
            <div className="mini-run__foot">
              <span><ShieldCheck size={12} /> redacted before inference</span>
              <span>{activeRun ? "streaming" : latestRun?.confidence ? `${Math.round(latestRun.confidence * 100)}% confidence` : "idle"}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="stats">
        <StatTile
          label="Mirrored issues"
          value={stats ? formatNumber(stats.open_issues) : <Skeleton className="sk-number" />}
          detail={stats ? `${stats.by_level.error ?? 0} errors · ${stats.by_level.warning ?? 0} warnings` : " "}
          icon={<TriangleAlert size={16} />}
          accent="pink"
        />
        <StatTile
          label="Seer runs"
          value={stats ? formatNumber(stats.seer_runs) : <Skeleton className="sk-number" />}
          detail={stats ? `${stats.seer_runs_last_7d} runs in the last 7 days` : " "}
          icon={<Bot size={16} />}
          accent="violet"
        />
        <StatTile
          label="Completed runs"
          value={stats ? formatNumber(stats.seer_completed) : <Skeleton className="sk-number" />}
          detail={stats ? `${stats.seer_failed} failed` : " "}
          icon={<GitPullRequest size={16} />}
          accent="green"
        />
        <StatTile
          label="Fixability rate"
          value={stats ? `${stats.fixability_rate}%` : "—"}
          detail={stats ? `${formatNumber(stats.tokens_last_7d)} local tokens in 7 days` : " "}
          icon={<Zap size={16} />}
          accent="amber"
        />
      </div>

      <div className="overview-grid">
        <section className="card queue-card">
          <SectionHead
            title="Sentry issue mirror"
            subtitle="Cached issues from the local workflow. Sentry remains the source of truth."
            action={<Link className="text-link" to="/issues">All issues <ChevronRight size={13} /></Link>}
          />
          {issues.loading && <QueueSkeleton />}
          {issues.error && <div className="notice notice--error">{issues.error.message}</div>}
          <div className="issue-list">
            {issues.data?.items.map((issue) => (
              <button
                className="issue-row"
                key={issue.id}
                onClick={() => navigate(`/issues/${issue.id}`)}
              >
                <span className={`issue-row__bar issue-row__bar--${issue.level}`} />
                <div className="issue-row__main">
                  <div className="issue-row__top">
                    <LevelBadge level={issue.level} />
                    <span className="issue-row__short">{issue.short_id}</span>
                    <span className="issue-row__time">{timeAgo(issue.last_seen)}</span>
                  </div>
                  <div className="issue-row__title">{issue.title}</div>
                  <div className="issue-row__meta">
                    <span className="mono">{issue.culprit || issue.project_slug}</span>
                    <span><Users size={12} /> {formatNumber(issue.user_count)}</span>
                    <span>{formatNumber(issue.event_count)} events</span>
                  </div>
                </div>
                <div className="issue-row__right">
                  <FixabilityMeter score={issue.fixability_score} compact />
                  {issue.latest_run_status ? (
                    <StatusBadge status={issue.latest_run_status} />
                  ) : (
                    <span className="issue-row__cta">Investigate <ArrowUpRight size={13} /></span>
                  )}
                </div>
              </button>
            ))}
          </div>
        </section>

        <aside className="overview-rail">
          <section className="card rail-card contract-card">
            <div className="contract-card__head">
              <span className="contract-card__icon"><Zap size={15} /></span>
              <div>
                <h3>Seer contract</h3>
                <p>Signed service endpoints Sentry already calls.</p>
              </div>
            </div>
            <div className="contract-list">
              <span><code>/v1/automation/agent/feature/run</code><b>Autofix</b></span>
              <span><code>/v1/automation/explorer/chat</code><b>Agent</b></span>
              <span><code>/v1/automation/explorer/state</code><b>State</b></span>
              <span><code>/v1/automation/summarize/issue</code><b>Summary</b></span>
            </div>
            <Link className="text-link" to="/settings">Review service configuration <ChevronRight size={13} /></Link>
          </section>

          <section className="card rail-card">
            <SectionHead
              title="Recent Seer runs"
              action={<Link className="text-link" to="/runs">View all</Link>}
            />
            {runs.loading && <QueueSkeleton rows={3} />}
            <div className="rail-runs">
              {runs.data?.items.map((run) => (
                <Link className="rail-run" key={run.id} to={`/runs/${run.id}`}>
                  <div className="rail-run__top">
                    <StatusBadge status={run.status} />
                    <span>{timeAgo(run.created_at)}</span>
                  </div>
                  <div className="rail-run__title">{run.summary || run.root_cause || "Collecting context…"}</div>
                  <div className="rail-run__meta">
                    <span>{run.kind}</span>
                    <span>{run.model || "local"}</span>
                    {run.status === "succeeded" && <span className="rail-run__patch"><GitPullRequest size={11} /> patch</span>}
                  </div>
                </Link>
              ))}
              {!runs.loading && !runs.data?.items.length && (
                <div className="rail-empty">No investigations yet.</div>
              )}
            </div>
          </section>

          <section className="card rail-card guardrails">
            <div className="guardrails__icon"><ShieldCheck size={18} /></div>
            <h3>Guardrails are on</h3>
            <p>
              Tokens are encrypted at rest. Telemetry is redacted before inference. Patches are
              validated with <code>git apply</code> and never merged automatically.
            </p>
            <div className="guardrails__tags">
              <span>Self-hosted</span>
              <span>Org AI switch</span>
              <span>Audit log</span>
            </div>
            <Link className="text-link" to="/settings">Review privacy controls <ChevronRight size={13} /></Link>
          </section>
        </aside>
      </div>
    </div>
  );
}

function QueueSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="queue-skeleton">
      {Array.from({ length: rows }).map((_, index) => (
        <div className="queue-skeleton__row" key={index}>
          <Skeleton className="sk-line sk-line--sm" />
          <Skeleton className="sk-line" />
          <Skeleton className="sk-line sk-line--md" />
        </div>
      ))}
    </div>
  );
}
