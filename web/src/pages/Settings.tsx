import {
  CheckCircle2,
  Copy,
  Database,
  GitBranch,
  KeyRound,
  Loader2,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Wifi,
} from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api } from "../api";
import { CopyButton, PageHeader, SectionHead, Toggle } from "../components/Ui";
import { formatNumber } from "../lib";
import { useOrg } from "../OrgContext";
import type { AppConfig, ConnectionTest, Organization, Repository, UsageSummary } from "../types";

export function SettingsPage({ config }: { config: AppConfig }) {
  const { org, refreshOrgs } = useOrg();
  const [form, setForm] = useState<Partial<Organization>>({});
  const [sentryToken, setSentryToken] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "error" | "muted"; text: string } | null>(null);
  const [connection, setConnection] = useState<ConnectionTest | null>(null);
  const [repos, setRepos] = useState<Repository[]>([]);
  const [repoForm, setRepoForm] = useState({
    name: "",
    provider: "github",
    url: "",
    default_branch: "main",
    local_path: "",
    access_token: "",
    project_slugs: "",
  });
  const [repoBusy, setRepoBusy] = useState(false);
  const [usage, setUsage] = useState<UsageSummary | null>(null);

  useEffect(() => {
    if (!org) return;
    setForm({
      name: org.name,
      sentry_url: org.sentry_url,
      sentry_org_slug: org.sentry_org_slug,
      ai_enabled: org.ai_enabled,
      auto_investigate: org.auto_investigate,
      auto_fix: org.auto_fix,
      fixability_threshold: org.fixability_threshold,
    });
    api.repos(org.id).then(setRepos).catch(() => undefined);
    api.usage().then(setUsage).catch(() => undefined);
  }, [org?.id]);

  if (!org) return null;
  const orgId = org.id;

  function update<K extends keyof Organization>(key: K, value: Organization[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function saveOrg(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setMessage(null);
    try {
      await api.updateOrg(orgId, {
        ...form,
        ...(sentryToken ? { sentry_token: sentryToken } : {}),
        ...(webhookSecret ? { webhook_secret: webhookSecret } : {}),
      });
      setSentryToken("");
      setWebhookSecret("");
      await refreshOrgs();
      setMessage({ tone: "ok", text: "Organization settings saved." });
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "Save failed" });
    } finally {
      setSaving(false);
    }
  }

  async function testConnection() {
    setTesting(true);
    setMessage(null);
    try {
      const result = await api.testSentry(orgId);
      setConnection(result);
      setMessage({ tone: result.ok ? "ok" : "error", text: result.message });
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "Connection test failed" });
    } finally {
      setTesting(false);
    }
  }

  async function sync() {
    setSyncing(true);
    setMessage(null);
    try {
      const result = await api.syncOrg(orgId);
      setMessage({ tone: "ok", text: `Synced ${result.synced} issues from Sentry.` });
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "Sync failed" });
    } finally {
      setSyncing(false);
    }
  }

  async function addRepo(event: FormEvent) {
    event.preventDefault();
    setRepoBusy(true);
    setMessage(null);
    try {
      const created = await api.createRepo(orgId, {
        ...repoForm,
        project_slugs: repoForm.project_slugs.split(",").map((value) => value.trim()).filter(Boolean),
      });
      setRepos((current) => [...current, created]);
      setRepoForm({ name: "", provider: "github", url: "", default_branch: "main", local_path: "", access_token: "", project_slugs: "" });
      setMessage({ tone: "ok", text: "Repository connected." });
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "Could not add repository" });
    } finally {
      setRepoBusy(false);
    }
  }

  async function removeRepo(repo: Repository) {
    if (!confirm(`Remove ${repo.name}?`)) return;
    try {
      await api.deleteRepo(repo.id);
      setRepos((current) => current.filter((item) => item.id !== repo.id));
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "Could not remove repository" });
    }
  }

  async function checkRepo(repo: Repository) {
    try {
      const result = await api.checkRepo(repo.id);
      setMessage({ tone: result.ok ? "ok" : "error", text: result.message });
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "Check failed" });
    }
  }

  async function indexRepo(repo: Repository) {
    setMessage({ tone: "muted", text: `Indexing ${repo.name}…` });
    try {
      const result = await api.indexRepo(repo.id);
      setMessage({
        tone: "ok",
        text: `Indexed ${result.indexed} files (${result.skipped} skipped).`,
      });
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "Index failed" });
    }
  }

  const seerSnippet = `SEER_AUTOFIX_URL=http://tweaker:8080
SEER_SUMMARIZATION_URL=http://tweaker:8080
SEER_DEFAULT_TIMEOUT=30
SEER_API_SHARED_SECRET=<same as Tweaker>
SEER_RPC_SHARED_SECRET=<same as Tweaker>`;

  return (
    <div className="page settings-page">
      <PageHeader
        eyebrow="Configuration"
        title="Service settings"
        description="Connect Sentry, map repositories, and control what the service is allowed to do."
        actions={
          <button className="btn btn--ghost" onClick={sync} disabled={syncing}>
            <RefreshCw size={15} className={syncing ? "spin" : undefined} />
            {syncing ? "Syncing…" : "Sync issues"}
          </button>
        }
      />

      {message && <div className={`notice notice--${message.tone === "ok" ? "success" : message.tone}`}>{message.text}</div>}

      <div className="settings-grid">
        <section className="card settings-card">
          <SectionHead
            title="Seer service contract"
            subtitle="Sentry's own Seer UI calls these endpoints. This panel is read-only."
          />
          <div className="contract-status">
            <div>
              <span className={`contract-dot ${config.seer_api_configured ? "contract-dot--on" : "contract-dot--off"}`} />
              <span>Inbound API secret</span>
              <strong>{config.seer_api_configured ? "configured" : "missing"}</strong>
            </div>
            <div>
              <span className={`contract-dot ${config.seer_rpc_configured ? "contract-dot--on" : "contract-dot--off"}`} />
              <span>Outbound RPC secret</span>
              <strong>{config.seer_rpc_configured ? "configured" : "missing"}</strong>
            </div>
            <div>
              <span className="contract-dot contract-dot--on" />
              <span>Sentry URL</span>
              <strong className="mono">{config.sentry_url}</strong>
            </div>
          </div>
          <div className="code-snippet">
            <div className="code-snippet__bar">
              <span>Sentry configuration</span>
              <CopyButton value={seerSnippet} label="Copy" />
            </div>
            <pre>{seerSnippet}</pre>
          </div>
          <p className="settings-note">
            Set the same shared secrets on both services, then restart Sentry. The exact Sentry
            feature flags vary by release; see <code>docs/seer-compatibility.md</code>.
          </p>
        </section>

        <section className="card settings-card">
          <SectionHead
            title="Sentry connection"
            subtitle="Used by the optional local workflow and issue mirror."
            action={
              <button className="btn btn--small btn--ghost" onClick={testConnection} disabled={testing}>
                {testing ? <Loader2 size={13} className="spin" /> : <Wifi size={13} />}
                {testing ? "Testing…" : "Test"}
              </button>
            }
          />
          <form className="form" onSubmit={saveOrg}>
            <div className="form-row">
              <label className="field">
                <span>Organization name</span>
                <input value={form.name ?? ""} onChange={(event) => update("name", event.target.value)} />
              </label>
              <label className="field">
                <span>Sentry URL</span>
                <input value={form.sentry_url ?? ""} onChange={(event) => update("sentry_url", event.target.value)} placeholder="https://sentry.example.com" />
              </label>
            </div>
            <div className="form-row">
              <label className="field">
                <span>Organization slug</span>
                <input value={form.sentry_org_slug ?? ""} onChange={(event) => update("sentry_org_slug", event.target.value)} placeholder="acme" />
              </label>
              <label className="field">
                <span>API token {org.has_sentry_token && <small>configured</small>}</span>
                <input type="password" value={sentryToken} onChange={(event) => setSentryToken(event.target.value)} placeholder={org.has_sentry_token ? "•••••••• (leave blank to keep)" : "sntrys_…"} />
              </label>
            </div>
            <label className="field">
              <span>Webhook client secret {org.has_webhook_secret && <small>configured</small>}</span>
              <input type="password" value={webhookSecret} onChange={(event) => setWebhookSecret(event.target.value)} placeholder={org.has_webhook_secret ? "•••••••• (leave blank to keep)" : "Optional for the legacy webhook workflow"} />
            </label>
            <div className="form-actions">
              <button className="btn btn--primary" disabled={saving}>
                {saving ? <Loader2 size={14} className="spin" /> : <CheckCircle2 size={14} />}
                {saving ? "Saving…" : "Save connection"}
              </button>
            </div>
          </form>
          {connection?.projects?.length ? (
            <div className="project-list">
              {connection.projects.map((project) => (
                <span key={project.slug}>{project.name || project.slug}</span>
              ))}
            </div>
          ) : null}
        </section>

        <section className="card settings-card">
          <SectionHead
            title="Automation"
            subtitle="Sentry controls the Seer workflow; these settings govern the local console."
          />
          <div className="toggle-stack">
            <Toggle
              checked={Boolean(form.ai_enabled)}
              onChange={(value) => update("ai_enabled", value)}
              label="Generative AI enabled"
              description="Disable to force the deterministic local analyzer."
            />
            <Toggle
              checked={Boolean(form.auto_investigate)}
              onChange={(value) => update("auto_investigate", value)}
              label="Auto-investigate mirrored issues"
              description="Only applies to the local mirror/poller, not Sentry's Seer UI."
            />
            <Toggle
              checked={Boolean(form.auto_fix)}
              onChange={(value) => update("auto_fix", value)}
              label="Publish autofix PRs automatically"
              description="Local workflow only. Sentry's Seer UI asks for approval."
            />
          </div>
          <label className="field field--range">
            <span>Local fixability threshold <strong>{form.fixability_threshold ?? 70}</strong></span>
            <input
              type="range"
              min={0}
              max={100}
              value={form.fixability_threshold ?? 70}
              onChange={(event) => update("fixability_threshold", Number(event.target.value))}
            />
          </label>
          <button className="btn btn--ghost" onClick={saveOrg} disabled={saving}>Save automation</button>
        </section>

        <section className="card settings-card">
          <SectionHead
            title="Usage this month"
            subtitle="Recorded across all Seer runs and model calls."
          />
          <div className="usage-grid">
            <div><strong>{usage ? formatNumber(usage.total_tokens) : "—"}</strong><span>tokens</span></div>
            <div><strong>{usage ? `$${usage.cost_usd.toFixed(2)}` : "—"}</strong><span>model cost</span></div>
            <div><strong>{usage ? formatNumber(usage.model_calls) : "—"}</strong><span>model calls</span></div>
            <div><strong>{usage ? formatNumber(usage.runs) : "—"}</strong><span>runs</span></div>
          </div>
          <p className="settings-note">
            Quotas are enforced by the Seer API per organization. Set them through
            <code>PUT /api/usage/quota/&lt;org_id&gt;</code>. No quota means unlimited.
          </p>
        </section>

        <section className="card settings-card">
          <SectionHead
            title="Source control"
            subtitle="Tweaker clones repositories through Sentry's project preferences."
            action={<span className="settings-badge"><KeyRound size={12} /> env tokens</span>}
          />
          <p className="settings-note">
            Set <code>TWEAKER_GITHUB_TOKEN</code> or <code>TWEAKER_GITLAB_TOKEN</code> in the
            service environment for private repositories and PR/MR creation. For air-gapped
            installs, map repositories with <code>TWEAKER_REPO_MAP</code>.
          </p>
          <div className="repo-list">
            {repos.map((repo) => (
              <div className="repo-row" key={repo.id}>
                <div className="repo-row__icon"><GitBranch size={15} /></div>
                <div className="repo-row__body">
                  <strong>{repo.name}</strong>
                  <span className="mono">{repo.url}</span>
                  <small>{repo.provider} · {repo.default_branch} · {repo.project_slugs.join(", ") || "all projects"}</small>
                </div>
                <div className="repo-row__actions">
                  <button className="icon-btn" onClick={() => checkRepo(repo)} title="Check access"><Wifi size={14} /></button>
                  <button className="icon-btn" onClick={() => indexRepo(repo)} title="Index code"><Database size={14} /></button>
                  <button className="icon-btn icon-btn--danger" onClick={() => removeRepo(repo)} title="Remove"><Trash2 size={14} /></button>
                </div>
              </div>
            ))}
            {!repos.length && <div className="muted-block">No repositories connected in the console. Sentry's project preferences still drive Seer runs.</div>}
          </div>
          <form className="repo-form" onSubmit={addRepo}>
            <div className="form-row">
              <label className="field"><span>Name</span><input value={repoForm.name} onChange={(event) => setRepoForm({ ...repoForm, name: event.target.value })} required /></label>
              <label className="field">
                <span>Provider</span>
                <select value={repoForm.provider} onChange={(event) => setRepoForm({ ...repoForm, provider: event.target.value })}>
                  <option value="github">GitHub</option>
                  <option value="gitlab">GitLab</option>
                  <option value="local">Local checkout</option>
                  <option value="generic">Generic</option>
                </select>
              </label>
            </div>
            <label className="field"><span>Clone URL</span><input value={repoForm.url} onChange={(event) => setRepoForm({ ...repoForm, url: event.target.value })} placeholder="https://github.com/acme/store.git" required /></label>
            <div className="form-row">
              <label className="field"><span>Default branch</span><input value={repoForm.default_branch} onChange={(event) => setRepoForm({ ...repoForm, default_branch: event.target.value })} /></label>
              <label className="field"><span>Local path (optional)</span><input value={repoForm.local_path} onChange={(event) => setRepoForm({ ...repoForm, local_path: event.target.value })} placeholder="/srv/checkouts/store" /></label>
            </div>
            <div className="form-row">
              <label className="field"><span>Access token (optional)</span><input type="password" value={repoForm.access_token} onChange={(event) => setRepoForm({ ...repoForm, access_token: event.target.value })} /></label>
              <label className="field"><span>Project slugs</span><input value={repoForm.project_slugs} onChange={(event) => setRepoForm({ ...repoForm, project_slugs: event.target.value })} placeholder="backend, frontend" /></label>
            </div>
            <button className="btn btn--ghost" disabled={repoBusy}>
              {repoBusy ? <Loader2 size={14} className="spin" /> : <Plus size={14} />} Add repository
            </button>
          </form>
        </section>

        <section className="card settings-card settings-card--wide">
          <SectionHead title="Privacy and guardrails" subtitle="Defaults are conservative." />
          <div className="guardrail-grid">
            <div><ShieldCheck size={16} /><strong>Redaction before inference</strong><span>Tokens, DSNs, cookies, auth headers, and private keys are stripped from prompts.</span></div>
            <div><GitBranch size={16} /><strong>Patch validation</strong><span>Unsafe paths, traversal, binaries, deletions, and symlinks are rejected.</span></div>
            <div><KeyRound size={16} /><strong>Secrets at rest</strong><span>Console integration credentials are encrypted with Fernet.</span></div>
            <div><Copy size={16} /><strong>Audit trail</strong><span>Run creation, failures, publishing, and configuration changes are recorded.</span></div>
          </div>
        </section>
      </div>
    </div>
  );
}
