import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  CircleDashed,
  GitPullRequest,
  Loader2,
  Terminal,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { DiffView } from "../components/Code";
import { EmptyState, ErrorNotice, RichText, Skeleton } from "../components/Ui";
import { useAsync, useDocumentTitle, usePolling } from "../hooks";
import { formatDate } from "../lib";
import type { SeerRunBlock, SeerRunDetail } from "../types";

export function RunPage() {
  const { runId = "" } = useParams();
  const run = useAsync(() => api.seerRun(Number(runId)), [runId]);
  useDocumentTitle(`Seer run #${runId}`);
  const processing = run.data?.summary.status === "processing";
  usePolling(() => run.refresh().catch(() => undefined), Boolean(processing), 1500);

  if (run.error) return <ErrorNotice error={run.error} onRetry={run.refresh} />;
  if (!run.data) {
    return (
      <div className="page">
        <Skeleton className="sk-title" />
        <Skeleton className="sk-block" />
      </div>
    );
  }

  const data: SeerRunDetail = run.data;
  const session = data.state.session;

  return (
    <div className="page">
      <div className="run-page__head">
        <Link className="back-link" to="/runs"><ArrowLeft size={13} /> Seer runs</Link>
        <div className="run-page__title">
          <div>
            <div className="eyebrow">Seer run #{session.run_id}</div>
            <h1>{data.summary.title || "Untitled Seer run"}</h1>
            <p>
              {data.summary.feature_id || data.summary.kind} · org {data.summary.org_id}
              {data.summary.group_id ? ` · issue ${data.summary.group_id}` : ""} · updated {formatDate(session.updated_at)}
            </p>
          </div>
          <span className={`run-status run-status--${session.status}`}>
            {session.status === "processing" ? <Loader2 size={12} className="spin" /> : session.status === "completed" ? <CheckCircle2 size={12} /> : session.status === "error" ? <AlertTriangle size={12} /> : <CircleDashed size={12} />}
            {session.status}
          </span>
        </div>
      </div>

      {session.failure_reason && (
        <div className="notice notice--warning">
          Failure reason: <code>{session.failure_reason}</code>
        </div>
      )}

      <div className="seer-blocks">
        {session.blocks.map((block) => (
          <SeerBlock key={block.id} block={block} />
        ))}
        {!session.blocks.length && (
          <EmptyState
            icon={<Terminal size={22} />}
            title="No blocks yet"
            body="Tweaker is collecting context, or this run has not produced a response yet."
          />
        )}
      </div>

      {Object.values(session.repo_pr_states ?? {}).length > 0 && (
        <div className="card">
          <h3 className="card-title"><GitPullRequest size={15} /> Pull requests</h3>
          <div className="pr-list">
            {Object.entries(session.repo_pr_states).map(([repo, state]) => (
              <div className="pr-row" key={repo}>
                <div>
                  <strong>{repo}</strong>
                  <span>{String(state.pr_creation_status ?? "unknown")}</span>
                </div>
                {state.pr_url ? (
                  <a className="btn btn--small btn--primary" href={String(state.pr_url)} target="_blank" rel="noreferrer">
                    View PR
                  </a>
                ) : (
                  <span className="muted">{String(state.pr_creation_error ?? "")}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <details className="raw-details">
        <summary>Raw run payload</summary>
        <pre>{JSON.stringify({ payload: data.payload, metadata: data.metadata }, null, 2)}</pre>
      </details>
    </div>
  );
}

function SeerBlock({ block }: { block: SeerRunBlock }) {
  const step = block.message.metadata?.step;
  const artifacts = block.artifacts ?? [];
  const patches = block.merged_file_patches ?? block.file_patches ?? [];
  const root = artifacts.find((item) => item.key === "root_cause");
  const solution = artifacts.find((item) => item.key === "solution");

  return (
    <article className={`seer-block seer-block--${block.message.role}`}>
      <div className="seer-block__head">
        <span className="seer-block__role">{block.message.role === "user" ? "Sentry user" : "Tweaker"}</span>
        {step && <span className="seer-block__step">{step}</span>}
        <span className="seer-block__time">{formatDate(block.timestamp)}</span>
      </div>
      {block.message.content && <RichText text={block.message.content} />}

      {block.tool_results && block.tool_results.length > 0 && (
        <div className="tool-list">
          {block.tool_results.map((result) => (
            <details className="tool-row" key={result.tool_call_id}>
              <summary>
                <Terminal size={12} />
                <span>{result.tool_call_function}</span>
                <small>tool result</small>
              </summary>
              <pre>{result.content || "(empty)"}</pre>
            </details>
          ))}
        </div>
      )}

      {root?.data && (
        <div className="artifact artifact--root">
          <h4>Root cause</h4>
          <p>{String(root.data.one_line_description ?? "")}</p>
          {Boolean(root.data.cause) && <RichText text={String(root.data.cause)} />}
          {Array.isArray(root.data.five_whys) && (
            <ol className="plan">
              {(root.data.five_whys as string[]).map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ol>
          )}
        </div>
      )}

      {solution?.data && (
        <div className="artifact artifact--solution">
          <h4>Solution</h4>
          <p>{String(solution.data.one_line_summary ?? "")}</p>
          <ol className="plan">
            {(solution.data.steps as { title: string; description: string }[] | undefined)?.map((item, index) => (
              <li key={index}><strong>{item.title}:</strong> {item.description}</li>
            ))}
          </ol>
        </div>
      )}

      {patches.map((patch, index) => (
        <div className="artifact artifact--patch" key={`${patch.repo_name}-${index}`}>
          <h4>{patch.repo_name} · {patch.patch.path}</h4>
          <DiffView patch={patch.diff} />
        </div>
      ))}
    </article>
  );
}
