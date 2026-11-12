from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)


class SentryRPCError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SentryRPCClient:
    """Client for the Seer -> Sentry RPC endpoints.

    Sentry exposes ``POST /api/0/seer-rpc/{method}/`` to the Seer service. The
    body is ``{"args": {...}}`` and is signed with ``SEER_RPC_SHARED_SECRET``.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        shared_secret: str | None = None,
        viewer_context: str | None = None,
        timeout: float | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.sentry_url).rstrip("/")
        secret = shared_secret or settings.seer_rpc_shared_secret
        if secret and secret.lstrip().startswith("["):
            try:
                values = json.loads(secret)
                secret = str(values[0]) if values else None
            except (json.JSONDecodeError, IndexError, TypeError):
                pass
        self.shared_secret = secret
        self.viewer_context = viewer_context
        self.timeout = timeout or settings.seer_request_timeout
        self._client = httpx.Client(
            timeout=self.timeout,
            verify=settings.sentry_verify_ssl,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SentryRPCClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def call(self, method: str, args: dict[str, Any]) -> Any:
        body = json.dumps({"args": args}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Seer-Referrer": "tweaker",
            "User-Agent": "Tweaker/0.1",
        }
        if self.shared_secret:
            signature = hmac.new(
                self.shared_secret.encode("utf-8"), body, hashlib.sha256
            ).hexdigest()
            headers["Authorization"] = f"Rpcsignature rpc0:{signature}"
        if self.viewer_context:
            headers["X-Viewer-Context"] = self.viewer_context
        url = f"{self.base_url}/api/0/seer-rpc/{method}/"
        response: httpx.Response | None = None
        for attempt in range(3):
            try:
                response = self._client.post(url, content=body, headers=headers)
            except httpx.RequestError as exc:
                if attempt < 2:
                    time.sleep(0.3 * (attempt + 1))
                    continue
                raise SentryRPCError(f"Could not reach Sentry RPC: {exc}") from exc
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                retry_after = response.headers.get("Retry-After", "0.5")
                try:
                    delay = min(float(retry_after), 5.0)
                except ValueError:
                    delay = 0.5
                time.sleep(delay)
                continue
            break
        if response is None:
            raise SentryRPCError("Sentry RPC request did not return a response")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text[:500]
            raise SentryRPCError(
                f"Sentry RPC {method} failed ({response.status_code}): {detail}",
                status_code=response.status_code,
            )
        if not response.content:
            return None
        return response.json()

    # -- methods used by the autofix and explorer features ------------------

    def get_issue_details(self, org_id: int, issue_id: str | int) -> dict[str, Any] | None:
        return self.call(
            "get_issue_details",
            {"organization_id": org_id, "issue_id": str(issue_id)},
        )

    def get_latest_issue_event(self, org_id: int, group_id: int) -> dict[str, Any]:
        data = self.call(
            "get_latest_issue_event",
            {"group_id": group_id, "organization_id": org_id},
        )
        return data if isinstance(data, dict) else {}

    def get_error_event_details(self, project_id: int, event_id: str) -> dict[str, Any]:
        data = self.call(
            "get_error_event_details",
            {"project_id": project_id, "event_id": event_id},
        )
        return data if isinstance(data, dict) else {}

    def get_issue_committers(self, org_id: int, issue_id: str | int) -> dict[str, Any]:
        data = self.call(
            "get_issue_committers",
            {"organization_id": org_id, "issue_id": str(issue_id)},
        )
        return data if isinstance(data, dict) else {}

    def get_issue_ownership(self, org_id: int, issue_id: str | int) -> dict[str, Any]:
        data = self.call(
            "get_issue_ownership",
            {"organization_id": org_id, "issue_id": str(issue_id)},
        )
        return data if isinstance(data, dict) else {}

    def get_project_preferences(self, org_id: int, project_id: int) -> dict[str, Any]:
        data = self.call(
            "get_project_preferences",
            {"organization_id": org_id, "project_id": project_id},
        )
        return data if isinstance(data, dict) else {}

    def get_repository_definition(
        self, org_id: int, repo_full_name: str, external_id: str | None = None
    ) -> dict[str, Any] | None:
        data = self.call(
            "get_repository_definition",
            {
                "organization_id": org_id,
                "repo_full_name": repo_full_name,
                "external_id": external_id,
            },
        )
        return data if isinstance(data, dict) else None

    def execute_issues_query(
        self,
        *,
        org_id: int,
        project_ids: list[int] | None = None,
        query: str = "is:unresolved",
        stats_period: str = "14d",
        limit: int = 25,
        sort: str | None = "date",
    ) -> list[dict[str, Any]]:
        data = self.call(
            "execute_issues_query",
            {
                "org_id": org_id,
                "project_ids": project_ids or [],
                "query": query,
                "stats_period": stats_period,
                "limit": limit,
                "sort": sort,
            },
        )
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            items = data.get("data")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []

    def get_event_details(
        self,
        *,
        org_id: int,
        issue_id: str | int | None = None,
        event_id: str | None = None,
        project_slug: str | None = None,
        include_breadcrumbs: bool = True,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "organization_id": org_id,
            "include_breadcrumbs": include_breadcrumbs,
        }
        if issue_id is not None:
            args["issue_id"] = str(issue_id)
        if event_id is not None:
            args["event_id"] = event_id
        if project_slug:
            args["project_slug"] = project_slug
        data = self.call("get_event_details", args)
        return data if isinstance(data, dict) else {}

    def get_trace_waterfall(self, org_id: int, trace_id: str) -> dict[str, Any]:
        data = self.call(
            "get_trace_waterfall",
            {"organization_id": org_id, "trace_id": trace_id},
        )
        return data if isinstance(data, dict) else {}

    def get_issue_filter_keys(
        self, org_id: int, project_ids: list[int] | None = None, stats_period: str = "14d"
    ) -> dict[str, Any]:
        data = self.call(
            "get_issue_filter_keys",
            {
                "org_id": org_id,
                "project_ids": project_ids or [],
                "stats_period": stats_period,
            },
        )
        return data if isinstance(data, dict) else {}

    def get_filter_key_values(
        self,
        *,
        org_id: int,
        project_ids: list[int] | None,
        attribute_key: str,
        substring: str | None = None,
        stats_period: str = "14d",
    ) -> list[dict[str, Any]]:
        data = self.call(
            "get_filter_key_values",
            {
                "org_id": org_id,
                "project_ids": project_ids or [],
                "attribute_key": attribute_key,
                "substring": substring,
                "stats_period": stats_period,
            },
        )
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    def get_attribute_names(
        self, org_id: int, project_ids: list[int] | None = None, item_type: str = "spans"
    ) -> dict[str, Any]:
        data = self.call(
            "get_attribute_names",
            {"org_id": org_id, "project_ids": project_ids or [], "item_type": item_type},
        )
        return data if isinstance(data, dict) else {}

    def get_attribute_values_with_substring(
        self,
        *,
        org_id: int,
        project_ids: list[int] | None,
        fields_with_substrings: list[dict[str, str]],
        item_type: str = "spans",
    ) -> dict[str, Any]:
        data = self.call(
            "get_attribute_values_with_substring",
            {
                "org_id": org_id,
                "project_ids": project_ids or [],
                "fields_with_substrings": fields_with_substrings,
                "item_type": item_type,
            },
        )
        return data if isinstance(data, dict) else {}

    def get_organization_projects(self, org_id: int) -> dict[str, Any]:
        data = self.call("get_organization_projects", {"org_id": org_id})
        return data if isinstance(data, dict) else {}

    def get_organization_slug(self, org_id: int) -> dict[str, Any]:
        data = self.call("get_organization_slug", {"org_id": org_id})
        return data if isinstance(data, dict) else {}

    def get_commit_author(self, org_id: int) -> dict[str, Any]:
        data = self.call("get_commit_author", {"org_id": org_id})
        return data if isinstance(data, dict) else {}

    def call_custom_tool(
        self, *, org_id: int, module_path: str, params: dict[str, Any]
    ) -> Any:
        return self.call(
            "call_custom_tool",
            {
                "module_path": module_path,
                "organization_id": org_id,
                **params,
            },
        )

    def get_team_members(self, org_id: int, team_slug: str) -> dict[str, Any]:
        data = self.call(
            "get_team_members",
            {"organization_id": org_id, "team_slug": team_slug},
        )
        return data if isinstance(data, dict) else {}

    def get_project_members(
        self, org_id: int, project_id: int, limit: int = 3
    ) -> dict[str, Any]:
        data = self.call(
            "get_project_members",
            {"organization_id": org_id, "project_id": project_id, "limit": limit},
        )
        return data if isinstance(data, dict) else {}

    def get_group_assignees(self, org_id: int, group_ids: list[int]) -> dict[str, Any]:
        data = self.call(
            "get_group_assignees",
            {"organization_id": org_id, "group_ids": group_ids},
        )
        return data if isinstance(data, dict) else {}

    def get_dsn(self, org_id: int, project_slug: str) -> dict[str, Any]:
        data = self.call(
            "get_dsn",
            {"organization_id": org_id, "project_slug": project_slug},
        )
        return data if isinstance(data, dict) else {}

    def get_replay_metadata(
        self, org_id: int, replay_id: str, project_slug: str | None = None
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"organization_id": org_id, "replay_id": replay_id}
        if project_slug:
            args["project_slug"] = project_slug
        data = self.call("get_replay_metadata", args)
        return data if isinstance(data, dict) else {}

    def get_replay_summary_logs(
        self, project_id: int, replay_id: str, num_segments: int = 10
    ) -> dict[str, Any]:
        data = self.call(
            "get_replay_summary_logs",
            {
                "project_id": project_id,
                "replay_id": replay_id,
                "num_segments": num_segments,
            },
        )
        return data if isinstance(data, dict) else {}

    def get_profile_flamegraph(
        self,
        org_id: int,
        profile_id: str,
        trace_id: str | None = None,
        span_description: str | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "organization_id": org_id,
            "profile_id": profile_id,
        }
        if trace_id:
            args["trace_id"] = trace_id
        if span_description:
            args["span_description"] = span_description
        data = self.call("get_profile_flamegraph", args)
        return data if isinstance(data, dict) else {}

    def get_log_attributes_for_trace(
        self, org_id: int, trace_id: str, message_substring: str = ""
    ) -> dict[str, Any]:
        data = self.call(
            "get_log_attributes_for_trace",
            {
                "org_id": org_id,
                "trace_id": trace_id,
                "message_substring": message_substring,
            },
        )
        return data if isinstance(data, dict) else {}

    def get_metric_attributes_for_trace(
        self, org_id: int, trace_id: str, metric_name: str = ""
    ) -> dict[str, Any]:
        data = self.call(
            "get_metric_attributes_for_trace",
            {
                "org_id": org_id,
                "trace_id": trace_id,
                "metric_name": metric_name,
            },
        )
        return data if isinstance(data, dict) else {}

    def get_metric_metadata(
        self,
        org_id: int,
        project_ids: list[int] | None = None,
        name_substrings: list[str] | None = None,
    ) -> dict[str, Any]:
        data = self.call(
            "get_metric_metadata",
            {
                "org_id": org_id,
                "project_ids": project_ids or [],
                "name_substrings": name_substrings or [],
            },
        )
        return data if isinstance(data, dict) else {}

    def get_baseline_tag_distribution(
        self,
        org_id: int,
        project_id: int,
        group_id: int,
        tag_keys: list[str],
    ) -> dict[str, Any]:
        data = self.call(
            "get_baseline_tag_distribution",
            {
                "organization_id": org_id,
                "project_id": project_id,
                "group_id": group_id,
                "tag_keys": tag_keys,
            },
        )
        return data if isinstance(data, dict) else {}

    def call_on_completion_hook(
        self, *, module_path: str, organization_id: int, run_id: int
    ) -> None:
        self.call(
            "call_on_completion_hook",
            {
                "module_path": module_path,
                "organization_id": organization_id,
                "run_id": run_id,
            },
        )

    def deliver_feature_result(
        self,
        *,
        organization_id: int,
        feature_id: str,
        run_uuid: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        self.call(
            "deliver_feature_result",
            {
                "organization_id": organization_id,
                "feature_id": feature_id,
                "run_uuid": run_uuid,
                "status": status,
                "result": result,
                "error": error,
            },
        )
