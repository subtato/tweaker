from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    sentry_url: Mapped[str] = mapped_column(String(500), default="https://sentry.io")
    sentry_org_slug: Mapped[str] = mapped_column(String(120), default="")
    sentry_token_encrypted: Mapped[str | None] = mapped_column(Text)
    sentry_installation_id: Mapped[str | None] = mapped_column(String(120), index=True)
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text)

    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_investigate: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_fix: Mapped[bool] = mapped_column(Boolean, default=False)
    fixability_threshold: Mapped[int] = mapped_column(Integer, default=70)
    default_repo_id: Mapped[str | None] = mapped_column(String(32))
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    repositories: Mapped[list[Repository]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class Repository(Base, TimestampMixin):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(40), default="github")
    url: Mapped[str] = mapped_column(String(800))
    default_branch: Mapped[str] = mapped_column(String(200), default="main")
    local_path: Mapped[str | None] = mapped_column(String(1000))
    access_token_encrypted: Mapped[str | None] = mapped_column(Text)
    project_slugs: Mapped[list[str]] = mapped_column(JSON, default=list)
    test_command: Mapped[str | None] = mapped_column(String(1000))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    organization: Mapped[Organization] = relationship(back_populates="repositories")


class Issue(Base, TimestampMixin):
    __tablename__ = "issues"
    __table_args__ = (
        UniqueConstraint("org_id", "sentry_id", name="uq_issue_org_sentry"),
        Index("ix_issue_org_status_seen", "org_id", "status", "last_seen"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    sentry_id: Mapped[str] = mapped_column(String(120), index=True)
    project_slug: Mapped[str] = mapped_column(String(200), default="")
    short_id: Mapped[str] = mapped_column(String(120), default="")
    title: Mapped[str] = mapped_column(Text, default="")
    culprit: Mapped[str] = mapped_column(Text, default="")
    level: Mapped[str] = mapped_column(String(40), default="error")
    status: Mapped[str] = mapped_column(String(40), default="unresolved")
    platform: Mapped[str] = mapped_column(String(80), default="")
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    user_count: Mapped[int] = mapped_column(Integer, default=0)
    permalink: Mapped[str] = mapped_column(String(1200), default="")
    issue_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    latest_event: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    fixability_score: Mapped[int | None] = mapped_column(Integer)
    fixability_reason: Mapped[str | None] = mapped_column(Text)
    fixability_signals: Mapped[list[str]] = mapped_column(JSON, default=list)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    repo_id: Mapped[str | None] = mapped_column(String(32), index=True)

    runs: Mapped[list[Run]] = relationship(back_populates="issue", cascade="all, delete-orphan")


class Run(Base, TimestampMixin):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_run_org_created", "org_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    issue_id: Mapped[str | None] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"), index=True
    )
    repo_id: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(40), default="investigate")
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    trigger: Mapped[str] = mapped_column(String(40), default="manual")
    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    summary: Mapped[str | None] = mapped_column(Text)
    root_cause: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    fixability_score: Mapped[int | None] = mapped_column(Integer)
    patch: Mapped[str | None] = mapped_column(Text)
    pr_url: Mapped[str | None] = mapped_column(String(1200))
    branch: Mapped[str | None] = mapped_column(String(300))
    error: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    run_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    issue: Mapped[Issue | None] = relationship(back_populates="runs")
    steps: Mapped[list[RunStep]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="RunStep.seq"
    )


class RunStep(Base):
    __tablename__ = "run_steps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(60), default="info")
    title: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(40), default="running")
    body: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[Run] = relationship(back_populates="steps")


class AskConversation(Base, TimestampMixin):
    __tablename__ = "ask_conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300), default="New question")
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class SeerRun(Base, TimestampMixin):
    """A run in the Seer wire format, addressed by the numeric run id Sentry uses.

    This is the primary state store for the drop-in service. The dashboard's
    ``Run`` model is separate and only records local console activity.
    """

    __tablename__ = "seer_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=lambda: str(uuid.uuid4()))
    org_id: Mapped[int] = mapped_column(Integer, index=True)
    org_slug: Mapped[str] = mapped_column(String(200), default="")
    project_id: Mapped[int | None] = mapped_column(Integer, index=True)
    group_id: Mapped[int | None] = mapped_column(Integer, index=True)
    feature_id: Mapped[str | None] = mapped_column(String(120), index=True)
    kind: Mapped[str] = mapped_column(String(40), default="feature")
    status: Mapped[str] = mapped_column(String(40), default="processing", index=True)
    title: Mapped[str] = mapped_column(String(500), default="")
    blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    run_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    repo_pr_states: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    coding_agents: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    queued_feedback: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    viewer_context: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    referrer: Mapped[str] = mapped_column(String(200), default="")
    sentry_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    external_idempotency_key: Mapped[str | None] = mapped_column(String(64), index=True)
    failure_reason: Mapped[str | None] = mapped_column(String(120))
    last_triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CodeIndexEntry(Base, TimestampMixin):
    __tablename__ = "code_index_entries"
    __table_args__ = (UniqueConstraint("repo_id", "path", name="uq_code_index_repo_path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[str] = mapped_column(String(300), index=True)
    path: Mapped[str] = mapped_column(String(1000))
    language: Mapped[str] = mapped_column(String(40), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    symbols: Mapped[str] = mapped_column(Text, default="")
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    mtime: Mapped[float] = mapped_column(Float, default=0.0)


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[int | None] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(80), default="llm")
    provider: Mapped[str] = mapped_column(String(80), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class OrgQuota(Base, TimestampMixin):
    __tablename__ = "org_quotas"

    org_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    monthly_token_limit: Mapped[int | None] = mapped_column(Integer)
    monthly_cost_limit: Mapped[float | None] = mapped_column(Float)
    monthly_run_limit: Mapped[int | None] = mapped_column(Integer)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SeerCodeReview(Base, TimestampMixin):
    __tablename__ = "seer_code_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(Integer, index=True)
    provider: Mapped[str] = mapped_column(String(80), default="")
    owner: Mapped[str] = mapped_column(String(200), default="")
    name: Mapped[str] = mapped_column(String(200), default="")
    pr_id: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(40), default="processing")
    summary: Mapped[str | None] = mapped_column(Text)
    comments: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SeerServiceMap(Base, TimestampMixin):
    __tablename__ = "seer_service_maps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    nodes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    edges: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class SeerGroupingRecord(Base, TimestampMixin):
    __tablename__ = "seer_grouping_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    hash: Mapped[str] = mapped_column(String(200), index=True)
    stacktrace: Mapped[str] = mapped_column(Text, default="")
    exception_type: Mapped[str] = mapped_column(String(300), default="")
    model: Mapped[str] = mapped_column(String(120), default="tweaker-similarity-v1")


class SeerSupergroup(Base, TimestampMixin):
    __tablename__ = "seer_supergroups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(Integer, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(500), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    error_type: Mapped[str] = mapped_column(String(300), default="")
    code_area: Mapped[str] = mapped_column(String(500), default="")
    group_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    project_ids: Mapped[list[int]] = mapped_column(JSON, default=list)


class SeerAssistedQuery(Base, TimestampMixin):
    __tablename__ = "seer_assisted_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(Integer, index=True)
    org_slug: Mapped[str] = mapped_column(String(200), default="")
    project_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    natural_language_query: Mapped[str] = mapped_column(Text, default="")
    strategy: Mapped[str] = mapped_column(String(80), default="Traces")
    status: Mapped[str] = mapped_column(String(40), default="processing")
    current_step: Mapped[str | None] = mapped_column(String(200))
    completed_steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    final_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    unsupported_reason: Mapped[str | None] = mapped_column(Text)
    viewer_context: Mapped[str | None] = mapped_column(Text)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str | None] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(200), default="system")
    action: Mapped[str] = mapped_column(String(120))
    target_type: Mapped[str] = mapped_column(String(80), default="")
    target_id: Mapped[str] = mapped_column(String(120), default="")
    event_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
