export type Level = "fatal" | "error" | "warning" | "info" | "debug" | string;
export type RunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type RunKind = "investigate" | "autofix" | "scan";

export interface AppConfig {
  app_name: string;
  version: string;
  auth_required: boolean;
  demo_mode: boolean;
  ai_provider: string;
  ai_model: string;
  ai_enabled: boolean;
  publishing_enabled: boolean;
  sentry_configured: boolean;
  mcp_endpoint: string;
  seer_api_configured: boolean;
  seer_rpc_configured: boolean;
  sentry_url: string;
}

export interface Organization {
  id: string;
  slug: string;
  name: string;
  sentry_url: string;
  sentry_org_slug: string;
  sentry_installation_id?: string | null;
  has_sentry_token: boolean;
  has_webhook_secret: boolean;
  ai_enabled: boolean;
  auto_investigate: boolean;
  auto_fix: boolean;
  fixability_threshold: number;
  default_repo_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Repository {
  id: string;
  org_id: string;
  name: string;
  provider: "github" | "gitlab" | "local" | "generic";
  url: string;
  default_branch: string;
  local_path?: string | null;
  has_access_token: boolean;
  project_slugs: string[];
  test_command?: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface IssueSummary {
  id: string;
  org_id: string;
  sentry_id: string;
  project_slug: string;
  short_id: string;
  title: string;
  culprit: string;
  level: Level;
  status: string;
  platform: string;
  first_seen?: string | null;
  last_seen?: string | null;
  event_count: number;
  user_count: number;
  permalink: string;
  fixability_score?: number | null;
  fixability_reason?: string | null;
  fixability_signals: string[];
  repo_id?: string | null;
  latest_run_id?: string | null;
  latest_run_status?: string | null;
}

export interface Frame {
  filename?: string;
  absPath?: string | null;
  module?: string | null;
  package?: string | null;
  function?: string | null;
  lineNo?: number | null;
  colNo?: number | null;
  inApp?: boolean;
  contextLine?: string | null;
  preContext?: string[];
  postContext?: string[];
  sourceLink?: string | null;
}

export interface ExceptionValue {
  type?: string;
  value?: string;
  mechanism?: unknown;
  stacktrace?: { frames?: Frame[] };
}

export interface Breadcrumb {
  timestamp?: string;
  category?: string;
  level?: string;
  message?: string;
  data?: Record<string, unknown>;
}

export interface SentryEvent {
  id?: string;
  eventID?: string;
  dateCreated?: string;
  title?: string;
  message?: string;
  culprit?: string;
  platform?: string;
  release?: string;
  environment?: string;
  level?: string;
  tags?: Record<string, string>;
  contexts?: Record<string, unknown>;
  sdk?: unknown;
  user?: Record<string, unknown>;
  breadcrumbs?: Breadcrumb[];
  exceptions?: ExceptionValue[];
  request?: { url?: string; method?: string };
  extra?: Record<string, unknown>;
}

export interface IssueDetail extends IssueSummary {
  issue_metadata: Record<string, unknown>;
  latest_event: SentryEvent;
  runs: RunSummary[];
}

export interface IssueListResponse {
  items: IssueSummary[];
  total: number;
  page: number;
  per_page: number;
}

export interface RunStep {
  id: string;
  seq: number;
  kind: string;
  title: string;
  status: "running" | "succeeded" | "failed" | "warning" | "skipped";
  body?: string | null;
  data: Record<string, unknown>;
  created_at: string;
}

export interface RunSummary {
  id: string;
  org_id: string;
  issue_id?: string | null;
  repo_id?: string | null;
  kind: RunKind | string;
  status: RunStatus;
  trigger: string;
  provider: string;
  model: string;
  summary?: string | null;
  root_cause?: string | null;
  confidence?: number | null;
  fixability_score?: number | null;
  patch?: string | null;
  pr_url?: string | null;
  branch?: string | null;
  error?: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface RunDetail extends RunSummary {
  patch?: string | null;
  run_metadata: {
    evidence?: { file?: string; line?: number | null; explanation?: string }[];
    fix_plan?: string[];
    tests?: string[];
    risk?: string;
    needs_human?: boolean;
    files?: string[];
    patch_error?: string;
    publish_error?: string;
    [key: string]: unknown;
  };
  steps: RunStep[];
  issue?: IssueSummary | null;
}

export interface RunListResponse {
  items: RunSummary[];
  total: number;
  page: number;
  per_page: number;
}

export interface Stats {
  open_issues: number;
  investigated: number;
  autofixes: number;
  fixability_rate: number;
  runs_last_7d: number;
  tokens_last_7d: number;
  average_confidence?: number | null;
  by_level: Record<string, number>;
  seer_runs: number;
  seer_completed: number;
  seer_failed: number;
  seer_runs_last_7d: number;
}

export interface Citation {
  type: string;
  id: string;
  title: string;
  url?: string | null;
}

export interface AskResponse {
  conversation_id: string;
  answer: string;
  citations: Citation[];
  follow_ups: string[];
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
}

export interface AuditEvent {
  id: string;
  actor: string;
  action: string;
  target_type: string;
  target_id: string;
  event_metadata: Record<string, unknown>;
  created_at: string;
}

export interface ConnectionTest {
  ok: boolean;
  message: string;
  organization?: string | null;
  projects: { id?: string; slug?: string; name?: string; platform?: string }[];
}

export interface SeerRunSummary {
  run_id: number;
  feature_id?: string | null;
  kind: string;
  status: "processing" | "completed" | "error" | "awaiting_user_input" | string;
  org_id: number;
  group_id?: number | null;
  project_id?: number | null;
  title: string;
  updated_at: string;
  blocks: number;
  repo_pr_states: Record<string, Record<string, unknown>>;
}

export interface SeerRunBlock {
  id: string;
  message: {
    role: string;
    content?: string | null;
    metadata?: Record<string, string> | null;
    tool_calls?: { id?: string | null; function: string; args: string }[] | null;
  };
  timestamp: string;
  artifacts?: { key: string; data: Record<string, unknown> | null; reason: string }[];
  tool_results?: {
    tool_call_id: string;
    tool_call_function: string;
    content?: string | null;
  }[] | null;
  merged_file_patches?: {
    repo_name: string;
    diff: string;
    patch: {
      path: string;
      type: string;
      added: number;
      removed: number;
      hunks: unknown[];
    };
  }[];
  file_patches?: SeerRunBlock["merged_file_patches"];
}

export interface SeerRunDetail {
  summary: SeerRunSummary;
  state: {
    session: {
      run_id: number;
      blocks: SeerRunBlock[];
      status: string;
      failure_reason?: string | null;
      updated_at: string;
      repo_pr_states: Record<string, Record<string, unknown>>;
      coding_agents?: Record<string, unknown>;
      metadata?: Record<string, unknown>;
    };
    feature_id?: string | null;
    sentry_run_id?: string | null;
  };
  payload: Record<string, unknown>;
  metadata: Record<string, unknown>;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  followUps?: string[];
  provider?: string;
  model?: string;
  pending?: boolean;
  error?: boolean;
}

export interface UsageSummary {
  org_id: string | null;
  since: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  model_calls: number;
  runs: number;
  by_kind: Record<string, number>;
  quota: {
    monthly_token_limit: number | null;
    monthly_cost_limit: number | null;
    monthly_run_limit: number | null;
    period_start: string | null;
  } | null;
}

export interface CodeReviewSummary {
  id: number;
  org_id: number;
  provider: string;
  repo: string;
  pr_id: string;
  status: string;
  conclusion?: string | null;
  summary?: string | null;
  comment_count: number;
  check_run_url?: string | null;
  review_url?: string | null;
  error?: string | null;
  created_at: string;
  finished_at?: string | null;
}
