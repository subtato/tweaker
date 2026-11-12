from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------


class OrganizationCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=200)
    sentry_url: str = "https://sentry.io"
    sentry_org_slug: str = ""
    sentry_token: str | None = None
    sentry_installation_id: str | None = None
    webhook_secret: str | None = None
    ai_enabled: bool = True
    auto_investigate: bool = False
    auto_fix: bool = False
    fixability_threshold: int = Field(default=70, ge=0, le=100)

    @field_validator("sentry_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")


class OrganizationUpdate(BaseModel):
    name: str | None = None
    sentry_url: str | None = None
    sentry_org_slug: str | None = None
    sentry_token: str | None = None
    sentry_installation_id: str | None = None
    webhook_secret: str | None = None
    ai_enabled: bool | None = None
    auto_investigate: bool | None = None
    auto_fix: bool | None = None
    fixability_threshold: int | None = Field(default=None, ge=0, le=100)
    default_repo_id: str | None = None


class OrganizationOut(ORMModel):
    id: str
    slug: str
    name: str
    sentry_url: str
    sentry_org_slug: str
    sentry_installation_id: str | None = None
    has_sentry_token: bool = False
    has_webhook_secret: bool = False
    ai_enabled: bool
    auto_investigate: bool
    auto_fix: bool
    fixability_threshold: int
    default_repo_id: str | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


class RepositoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    provider: Literal["github", "gitlab", "local", "generic"] = "github"
    url: str = Field(min_length=1, max_length=800)
    default_branch: str = "main"
    local_path: str | None = None
    access_token: str | None = None
    project_slugs: list[str] = Field(default_factory=list)
    test_command: str | None = None
    enabled: bool = True


class RepositoryUpdate(BaseModel):
    name: str | None = None
    provider: Literal["github", "gitlab", "local", "generic"] | None = None
    url: str | None = None
    default_branch: str | None = None
    local_path: str | None = None
    access_token: str | None = None
    project_slugs: list[str] | None = None
    test_command: str | None = None
    enabled: bool | None = None


class RepositoryOut(ORMModel):
    id: str
    org_id: str
    name: str
    provider: str
    url: str
    default_branch: str
    local_path: str | None = None
    has_access_token: bool = False
    project_slugs: list[str]
    test_command: str | None = None
    enabled: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------


class IssueSummary(ORMModel):
    id: str
    org_id: str
    sentry_id: str
    project_slug: str
    short_id: str
    title: str
    culprit: str
    level: str
    status: str
    platform: str
    first_seen: datetime | None
    last_seen: datetime | None
    event_count: int
    user_count: int
    permalink: str
    fixability_score: int | None
    fixability_reason: str | None
    fixability_signals: list[str] = Field(default_factory=list)
    repo_id: str | None = None
    latest_run_id: str | None = None
    latest_run_status: str | None = None


class IssueDetail(IssueSummary):
    issue_metadata: dict[str, Any] = Field(default_factory=dict)
    latest_event: dict[str, Any] = Field(default_factory=dict)
    runs: list[RunSummary] = Field(default_factory=list)


class IssueListResponse(BaseModel):
    items: list[IssueSummary]
    total: int
    page: int
    per_page: int


class InvestigateRequest(BaseModel):
    mode: Literal["investigate", "autofix"] = "investigate"
    repo_id: str | None = None
    publish: bool = False


class ScanRequest(BaseModel):
    force: bool = False


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class RunStepOut(ORMModel):
    id: str
    seq: int
    kind: str
    title: str
    status: str
    body: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RunSummary(ORMModel):
    id: str
    org_id: str
    issue_id: str | None
    repo_id: str | None
    kind: str
    status: str
    trigger: str
    provider: str
    model: str
    summary: str | None
    root_cause: str | None
    confidence: float | None
    fixability_score: int | None
    pr_url: str | None
    branch: str | None
    error: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunDetail(RunSummary):
    patch: str | None = None
    run_metadata: dict[str, Any] = Field(default_factory=dict)
    steps: list[RunStepOut] = Field(default_factory=list)
    issue: IssueSummary | None = None


class RunListResponse(BaseModel):
    items: list[RunSummary]
    total: int
    page: int
    per_page: int


class PublishRequest(BaseModel):
    branch: str | None = None
    title: str | None = None
    body: str | None = None
    draft: bool = False


# ---------------------------------------------------------------------------
# Ask
# ---------------------------------------------------------------------------


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    org_id: str | None = None
    issue_id: str | None = None
    conversation_id: str | None = None


class Citation(BaseModel):
    type: str
    id: str
    title: str
    url: str | None = None


class AskResponse(BaseModel):
    conversation_id: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


# ---------------------------------------------------------------------------
# Dashboard / settings
# ---------------------------------------------------------------------------


class StatsResponse(BaseModel):
    open_issues: int
    investigated: int
    autofixes: int
    fixability_rate: float
    runs_last_7d: int
    tokens_last_7d: int
    average_confidence: float | None
    by_level: dict[str, int] = Field(default_factory=dict)
    seer_runs: int = 0
    seer_completed: int = 0
    seer_failed: int = 0
    seer_runs_last_7d: int = 0


class AppConfigOut(BaseModel):
    app_name: str
    version: str
    auth_required: bool
    demo_mode: bool
    ai_provider: str
    ai_model: str
    ai_enabled: bool
    publishing_enabled: bool
    sentry_configured: bool
    mcp_endpoint: str
    seer_api_configured: bool = False
    seer_rpc_configured: bool = False
    sentry_url: str = ""


class ConnectionTestOut(BaseModel):
    ok: bool
    message: str
    organization: str | None = None
    projects: list[dict[str, Any]] = Field(default_factory=list)


class AuditEventOut(ORMModel):
    id: str
    actor: str
    action: str
    target_type: str
    target_id: str
    event_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class HealthOut(BaseModel):
    status: str
    version: str
    database: str
    runner: str


class SlackWebhookOut(BaseModel):
    response_type: str = "ephemeral"
    text: str
    blocks: list[dict[str, Any]] | None = None


class QuotaUpdate(BaseModel):
    monthly_token_limit: int | None = Field(default=None, ge=0)
    monthly_cost_limit: float | None = Field(default=None, ge=0)
    monthly_run_limit: int | None = Field(default=None, ge=0)
