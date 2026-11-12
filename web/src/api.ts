import type {
  AppConfig,
  AskResponse,
  AuditEvent,
  ConnectionTest,
  IssueDetail,
  IssueListResponse,
  Organization,
  Repository,
  RunDetail,
  RunListResponse,
  RunSummary,
  Stats,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";
const TOKEN_KEY = "tweaker.token";

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

export function setToken(token: string): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

function headers(extra?: HeadersInit): Headers {
  const result = new Headers(extra);
  const token = getToken();
  if (token) result.set("Authorization", `Bearer ${token}`);
  if (!result.has("Content-Type")) result.set("Content-Type", "application/json");
  return result;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: headers(options.headers),
  });
  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent("tweaker:unauthorized"));
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // Keep the HTTP status text.
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

export const api = {
  config: () => request<AppConfig>("/api/config"),
  me: () => request<{ authenticated: boolean }>("/api/me"),
  health: () => request<{ status: string; version: string }>("/api/health"),

  orgs: () => request<Organization[]>("/api/orgs"),
  createOrg: (body: Record<string, unknown>) =>
    request<Organization>("/api/orgs", { method: "POST", body: JSON.stringify(body) }),
  updateOrg: (id: string, body: Record<string, unknown>) =>
    request<Organization>(`/api/orgs/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  testSentry: (id: string) =>
    request<ConnectionTest>(`/api/orgs/${id}/test-sentry`, { method: "POST" }),
  listProjects: (id: string) =>
    request<{ items: { id?: string; slug?: string; name?: string; platform?: string }[] }>(
      `/api/orgs/${id}/projects`,
    ),
  syncOrg: (id: string, projectSlug?: string) =>
    request<{ synced: number }>(
      `/api/orgs/${id}/sync${qs({ project_slug: projectSlug, with_events: true })}`,
      { method: "POST" },
    ),

  repos: (orgId: string) => request<Repository[]>(`/api/orgs/${orgId}/repos`),
  createRepo: (orgId: string, body: Record<string, unknown>) =>
    request<Repository>(`/api/orgs/${orgId}/repos`, { method: "POST", body: JSON.stringify(body) }),
  updateRepo: (id: string, body: Record<string, unknown>) =>
    request<Repository>(`/api/repos/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteRepo: (id: string) => request<void>(`/api/repos/${id}`, { method: "DELETE" }),
  checkRepo: (id: string) =>
    request<{ ok: boolean; message: string; path?: string }>(`/api/repos/${id}/check`, {
      method: "POST",
    }),
  indexRepo: (id: string) =>
    request<{ repo_id: string; indexed: number; skipped: number; removed: number }>(
      `/api/repos/${id}/index`,
      { method: "POST" },
    ),
  repoIndex: (id: string) =>
    request<{ files: number; lines: number }>(`/api/repos/${id}/index`),

  issues: (params: Record<string, string | number | boolean | null | undefined>) =>
    request<IssueListResponse>(`/api/issues${qs(params)}`),
  issue: (id: string) => request<IssueDetail>(`/api/issues/${id}`),
  investigate: (id: string, body: { mode: "investigate" | "autofix"; repo_id?: string; publish?: boolean }) =>
    request<RunSummary>(`/api/issues/${id}/investigate`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  scan: (id: string) =>
    request<IssueDetail>(`/api/issues/${id}/scan`, { method: "POST", body: JSON.stringify({}) }),
  refreshIssue: (id: string) =>
    request<IssueDetail>(`/api/issues/${id}/refresh`, { method: "POST" }),

  runs: (params: Record<string, string | number | boolean | null | undefined>) =>
    request<RunListResponse>(`/api/runs${qs(params)}`),
  run: (id: string) => request<RunDetail>(`/api/runs/${id}`),
  cancelRun: (id: string) => request<RunSummary>(`/api/runs/${id}/cancel`, { method: "POST" }),
  retryRun: (id: string) => request<RunSummary>(`/api/runs/${id}/retry`, { method: "POST" }),
  publishRun: (id: string, body: Record<string, unknown> = {}) =>
    request<RunSummary>(`/api/runs/${id}/publish`, { method: "POST", body: JSON.stringify(body) }),

  seerRuns: (params: Record<string, string | number | boolean | null | undefined>) =>
    request<{ items: import("./types").SeerRunSummary[]; total: number }>(
      `/api/seer-runs${qs(params)}`,
    ),
  seerRun: (id: number) => request<import("./types").SeerRunDetail>(`/api/seer-runs/${id}`),
  codeReviews: () => request<{ items: import("./types").CodeReviewSummary[] }>("/api/code-reviews?limit=5"),

  stats: (orgId: string) => request<Stats>(`/api/stats${qs({ org_id: orgId })}`),
  usage: (orgId?: string) => request<import("./types").UsageSummary>(`/api/usage${qs({ org_id: orgId })}`),
  audit: (orgId: string) => request<AuditEvent[]>(`/api/audit${qs({ org_id: orgId, limit: 50 })}`),

  ask: (body: {
    question: string;
    org_id: string;
    issue_id?: string;
    conversation_id?: string;
  }) => request<AskResponse>("/api/ask", { method: "POST", body: JSON.stringify(body) }),
  conversations: (orgId: string) =>
    request<{ items: { id: string; title: string; updated_at: string }[] }>(
      `/api/ask/conversations${qs({ org_id: orgId })}`,
    ),
  suggestions: (orgId: string) =>
    request<{ items: string[] }>(`/api/ask/suggestions${qs({ org_id: orgId })}`),

  async downloadPatch(runId: string): Promise<void> {
    const response = await fetch(`${API_BASE}/api/runs/${runId}/patch`, { headers: headers() });
    if (!response.ok) throw new ApiError(response.status, "Could not download patch");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `tweaker-${runId.slice(0, 8)}.patch`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  },
};
