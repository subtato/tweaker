import { ArrowRight, KeyRound, Loader2, ShieldCheck } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api, ApiError, clearToken, getToken, setToken } from "./api";
import { Logo, LogoMark } from "./components/Logo";
import { Layout } from "./components/Layout";
import { OrgProvider } from "./OrgContext";
import type { AppConfig, Organization } from "./types";
import { Overview } from "./pages/Overview";
import { Issues } from "./pages/Issues";
import { IssuePage } from "./pages/IssuePage";
import { Runs } from "./pages/Runs";
import { RunPage } from "./pages/RunPage";
import { Ask } from "./pages/Ask";
import { SettingsPage } from "./pages/Settings";
import { Setup } from "./pages/Setup";

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [orgs, setOrgs] = useState<Organization[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [needsToken, setNeedsToken] = useState(false);
  const [boot, setBoot] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .config()
      .then((next) => {
        if (cancelled) return;
        setConfig(next);
        if (next.auth_required && !getToken()) {
          setNeedsToken(true);
          setOrgs([]);
          return;
        }
        return api.orgs().then((items) => {
          if (!cancelled) setOrgs(items);
        });
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) setNeedsToken(true);
        else setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [boot]);

  useEffect(() => {
    function unauthorized() {
      if (config?.auth_required) {
        setNeedsToken(true);
        setOrgs([]);
      }
    }
    window.addEventListener("tweaker:unauthorized", unauthorized);
    return () => window.removeEventListener("tweaker:unauthorized", unauthorized);
  }, [config?.auth_required]);

  if (error) {
    return (
      <div className="boot">
        <div className="boot__card">
          <LogoMark size={46} />
          <h1>Tweaker couldn't start</h1>
          <p>{error}</p>
          <button className="btn btn--primary" onClick={() => setBoot((value) => value + 1)}>
            Try again
          </button>
        </div>
      </div>
    );
  }

  if (!config || orgs === null) {
    return (
      <div className="boot">
        <div className="boot__pulse">
          <LogoMark size={54} />
        </div>
        <div className="boot__label">Connecting to Tweaker</div>
      </div>
    );
  }

  if (needsToken) {
    return (
      <TokenGate
        config={config}
        onAuthenticated={() => {
          setNeedsToken(false);
          setOrgs(null);
          setBoot((value) => value + 1);
        }}
      />
    );
  }

  if (!orgs.length) {
    return (
      <Setup
        config={config}
        onCreated={async () => {
          setOrgs(await api.orgs());
        }}
      />
    );
  }

  return (
    <OrgProvider initialOrgs={orgs}>
      <Routes>
        <Route element={<Layout config={config} />}>
          <Route index element={<Overview />} />
          <Route path="issues" element={<Issues />} />
          <Route path="issues/:issueId" element={<IssuePage />} />
          <Route path="runs" element={<Runs />} />
          <Route path="runs/:runId" element={<RunPage />} />
          <Route path="ask" element={<Ask />} />
          <Route path="settings" element={<SettingsPage config={config} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </OrgProvider>
  );
}

function TokenGate({ config, onAuthenticated }: { config: AppConfig; onAuthenticated: () => void }) {
  const [value, setValue] = useState(getToken());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!value.trim()) return;
    setBusy(true);
    setError(null);
    setToken(value.trim());
    try {
      await api.me();
      onAuthenticated();
    } catch (err) {
      clearToken();
      setError(err instanceof Error ? err.message : "Invalid token");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <div className="auth__art">
        <Logo />
        <div className="auth__glow" />
        <div className="auth__quote">
          <span>“</span>
          <p>Root cause, evidence, and a patch — before the on-call engineer opens their editor.</p>
        </div>
        <div className="auth__badges">
          <span><ShieldCheck size={13} /> Self-hosted</span>
          <span>Bring your own model</span>
          <span>Sentry-native</span>
        </div>
      </div>
      <div className="auth__panel">
        <form className="auth__form" onSubmit={submit}>
          <div className="auth__icon"><KeyRound size={20} /></div>
          <div className="eyebrow">Protected workspace</div>
          <h1>Connect to {config.app_name}</h1>
          <p>Enter the service token configured in <code>TWEAKER_AUTH_TOKEN</code>.</p>
          <label className="field">
            <span>Service token</span>
            <input
              type="password"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder="tweaker_…"
              autoFocus
            />
          </label>
          {error && <div className="notice notice--error">{error}</div>}
          <button className="btn btn--primary btn--wide" disabled={busy || !value.trim()}>
            {busy ? <Loader2 size={15} className="spin" /> : <ArrowRight size={15} />}
            {busy ? "Verifying…" : "Open workspace"}
          </button>
          <small className="auth__hint">
            The token is stored in this browser only. For team access, put Tweaker behind your SSO proxy.
          </small>
        </form>
      </div>
    </div>
  );
}
