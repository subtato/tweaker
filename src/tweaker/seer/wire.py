from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WireModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class FeatureRunRequest(WireModel):
    feature_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    agent_run_options: dict[str, Any] = Field(default_factory=dict)
    user_org_context: dict[str, Any] = Field(default_factory=dict)
    proxy_headers: dict[str, str] | None = None
    referrer: str = "unknown"
    ref: str | None = None
    external_idempotency_key: str | None = None


class ChatRequest(WireModel):
    organization_id: int
    query: str = ""
    run_id: int | None = None
    ref: str | None = None
    external_idempotency_key: str | None = None
    insert_index: int | None = None
    on_page_context: str | None = None
    page_name: str | None = None
    page_location: dict[str, Any] | None = None
    sent_at: list[str] | None = None
    user_org_context: dict[str, Any] | None = None
    intelligence_level: str = "medium"
    reasoning_effort: str = "medium"
    is_interactive: bool = False
    project_id: int | None = None
    query_metadata: dict[str, str] = Field(default_factory=dict)
    artifact_key: str | None = None
    artifact_schema: dict[str, Any] | None = None
    custom_tools: list[dict[str, Any]] = Field(default_factory=list)
    on_completion_hook: dict[str, Any] | None = None
    category_key: str | None = None
    category_value: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    agent_run_options: dict[str, Any] = Field(default_factory=dict)
    max_iterations: int | None = None
    proxy_headers: dict[str, str] | None = None
    ui_tools: str | None = None
    monitoring_providers: list[dict[str, Any]] = Field(default_factory=list)
    available_monitoring_providers: list[dict[str, Any]] = Field(default_factory=list)


class StateRequest(WireModel):
    run_id: int
    organization_id: int


class RunsByIdsRequest(WireModel):
    run_ids: list[int] = Field(default_factory=list)


class ReposRequest(WireModel):
    run_id: int
    organization_id: int


class PrStateRequest(WireModel):
    organization_id: int
    provider: str
    pr_id: str


class UpdateRequest(WireModel):
    run_id: int
    organization_id: int
    payload: dict[str, Any] = Field(default_factory=dict)


class SummarizeIssueRequest(WireModel):
    group_id: int
    issue: dict[str, Any] = Field(default_factory=dict)
    trace_tree: dict[str, Any] | None = None
    organization_slug: str = ""
    organization_id: int
    project_id: int
    experiment_variant: str | None = None


class FixabilityRequest(WireModel):
    group_id: int
    organization_slug: str = ""
    organization_id: int
    project_id: int
    summary: dict[str, Any] | None = None


class LlmGenerateRequest(WireModel):
    provider: str = ""
    model: str = ""
    referrer: str = ""
    prompt: str = ""
    system_prompt: str = ""
    temperature: float = 0.0
    max_tokens: int = 1024
    response_schema: dict[str, Any] | None = None
    timeout: float | None = None
    reasoning: str | None = None
    conversation_id: str | None = None


class OneShotRequest(WireModel):
    oneshot_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class SupergroupClusterRequest(WireModel):
    group_id: int | None = None
    issue: dict[str, Any] = Field(default_factory=dict)
    organization_slug: str = ""
    organization_id: int
    project_id: int | None = None


class SupergroupGetRequest(WireModel):
    organization_id: int
    supergroup_id: int


class SupergroupByGroupIdsRequest(WireModel):
    organization_id: int
    group_ids: list[int] = Field(default_factory=list)


class ServiceMapUpdateRequest(WireModel):
    organization_id: int
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class AssistedQueryStartRequest(WireModel):
    org_id: int
    org_slug: str = ""
    project_ids: list[int] = Field(default_factory=list)
    natural_language_query: str = ""
    strategy: str = "Traces"
    options: dict[str, Any] = Field(default_factory=dict)
    external_idempotency_key: str | None = None
    user_email: str | None = None
    timezone: str | None = None


class AssistedQueryStateRequest(WireModel):
    run_id: int
    organization_id: int


class TranslateAgenticRequest(WireModel):
    org_id: int
    org_slug: str = ""
    project_ids: list[int] = Field(default_factory=list)
    natural_language_query: str = ""
    strategy: str = "Traces"
    options: dict[str, Any] = Field(default_factory=dict)
    user_email: str | None = None
    timezone: str | None = None


class CompareCohortRequest(WireModel):
    baseline: list[dict[str, Any]] = Field(default_factory=list)
    outliers: list[dict[str, Any]] = Field(default_factory=list)
    total_baseline: int = 0
    total_outliers: int = 0
    config: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class CodingAgentStateUpdate(WireModel):
    status: str | None = None
    agent_url: str | None = None
    results: list[dict[str, Any]] | None = None


class CodingAgentStateUpdateRequest(WireModel):
    agent_id: str
    updates: CodingAgentStateUpdate = Field(default_factory=CodingAgentStateUpdate)


class StoreCodingAgentStatesRequest(WireModel):
    run_id: int
    coding_agent_states: list[dict[str, Any]] = Field(default_factory=list)


class DelegatedAgentMatchRequest(WireModel):
    organization_id: int
    pull_request_id: int | None = None
    pr_url: str = ""
    repo: dict[str, Any] = Field(default_factory=dict)
    head_branch: str = ""
    provider: str = ""
    group_ids: list[int] = Field(default_factory=list)


class ExportIndexesRequest(WireModel):
    org_id: int


class GenericPayload(WireModel):
    pass
