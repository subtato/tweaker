import { ArrowRight, Loader2, ShieldCheck, Terminal } from "lucide-react";
import { FormEvent, useState } from "react";
import { api } from "../api";
import { Logo, LogoMark } from "../components/Logo";
import type { AppConfig } from "../types";

export function Setup({ config, onCreated }: { config: AppConfig; onCreated: () => Promise<void> }) {
  const [form, setForm] = useState({
    name: "",
    slug: "",
    sentry_url: "https://sentry.example.com",
    sentry_org_slug: "",
    sentry_token: "",
    webhook_secret: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createOrg(form);
      await onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the organization");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="setup">
      <div className="setup__brand">
        <Logo />
        <div className="setup__pitch">
          <div className="eyebrow">First run</div>
          <h1>Point Sentry's Seer UI at an open-source backend.</h1>
          <p>
            Create the local organization record, connect Sentry, and set the same Seer shared
            secrets on both sides. Sentry remains the primary interface.
          </p>
          <div className="setup__points">
            <span><ShieldCheck size={14} /> Signed service contract</span>
            <span><Terminal size={14} /> Bring your own model</span>
            <span><ShieldCheck size={14} /> Self-hosted by design</span>
          </div>
          <div className="setup__mark"><LogoMark size={120} /></div>
        </div>
      </div>
      <div className="setup__panel">
        <form className="setup__form" onSubmit={submit}>
          <div className="eyebrow">Organization</div>
          <h2>Create your workspace</h2>
          <p>This is the local operator record. Sentry's organization is the source of truth for issues.</p>
          <div className="form-row">
            <label className="field">
              <span>Name</span>
              <input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="Acme Commerce" required />
            </label>
            <label className="field">
              <span>Slug</span>
              <input value={form.slug} onChange={(event) => setForm({ ...form, slug: event.target.value })} placeholder="acme" pattern="[a-z0-9-]+" required />
            </label>
          </div>
          <label className="field">
            <span>Sentry URL</span>
            <input value={form.sentry_url} onChange={(event) => setForm({ ...form, sentry_url: event.target.value })} required />
          </label>
          <div className="form-row">
            <label className="field">
              <span>Sentry organization slug</span>
              <input value={form.sentry_org_slug} onChange={(event) => setForm({ ...form, sentry_org_slug: event.target.value })} placeholder="acme" />
            </label>
            <label className="field">
              <span>Sentry API token (optional)</span>
              <input type="password" value={form.sentry_token} onChange={(event) => setForm({ ...form, sentry_token: event.target.value })} placeholder="sntrys_…" />
            </label>
          </div>
          <label className="field">
            <span>Webhook client secret (optional)</span>
            <input type="password" value={form.webhook_secret} onChange={(event) => setForm({ ...form, webhook_secret: event.target.value })} />
          </label>
          {error && <div className="notice notice--error">{error}</div>}
          <button className="btn btn--primary btn--wide" disabled={busy}>
            {busy ? <Loader2 size={15} className="spin" /> : <ArrowRight size={15} />}
            {busy ? "Creating…" : "Create workspace"}
          </button>
          <small className="auth__hint">
            The Seer secrets are environment variables, not console settings. Set
            <code>SEER_API_SHARED_SECRET</code> and <code>SEER_RPC_SHARED_SECRET</code> on both
            services. {config.demo_mode ? "Demo mode is on." : ""}
          </small>
        </form>
      </div>
    </div>
  );
}
