from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)


class SentryError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class SentryClient:
    base_url: str
    token: str
    timeout: float = 20.0
    verify_ssl: bool = True

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Sentry URL must be an absolute http(s) URL")
        self.base_url = self.base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=self.timeout,
            verify=self.verify_ssl,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "User-Agent": "Tweaker/0.1",
            },
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SentryClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # -- low level ---------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}/api/0/{path.lstrip('/')}"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                raise SentryError(f"Could not reach Sentry: {exc}") from exc

            if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                retry_after = response.headers.get("Retry-After", "0.5")
                try:
                    delay = min(float(retry_after), 5.0)
                except ValueError:
                    delay = 0.5
                time.sleep(delay)
                continue
            if response.status_code >= 400:
                detail = _error_detail(response)
                raise SentryError(
                    f"Sentry API {response.status_code}: {detail}",
                    status_code=response.status_code,
                )
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
        raise SentryError(f"Sentry request failed: {last_error}")

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    # -- resources ---------------------------------------------------------

    def test_connection(self) -> dict[str, Any]:
        org = self._get("organizations/")
        if isinstance(org, list):
            return {"ok": True, "organizations": org}
        return {"ok": True, "organizations": []}

    def list_projects(self, org_slug: str) -> list[dict[str, Any]]:
        data = self._get(
            f"organizations/{quote(org_slug, safe='')}/projects/",
            params={"per_page": 100},
        )
        return data if isinstance(data, list) else []

    def get_organization(self, org_slug: str) -> dict[str, Any]:
        data = self._get(f"organizations/{quote(org_slug, safe='')}/")
        return data if isinstance(data, dict) else {}

    def list_issues(
        self,
        org_slug: str,
        project_slug: str | None = None,
        query: str = "is:unresolved",
        limit: int = 25,
        stats_period: str = "14d",
        sort: str = "date",
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "query": query,
            "limit": limit,
            "statsPeriod": stats_period,
            "sort": sort,
        }
        if project_slug:
            path = f"projects/{quote(org_slug, safe='')}/{quote(project_slug, safe='')}/issues/"
        else:
            path = f"organizations/{quote(org_slug, safe='')}/issues/"
        data = self._get(path, params=params)
        return data if isinstance(data, list) else []

    def get_issue(self, issue_id: str) -> dict[str, Any]:
        data = self._get(f"issues/{quote(issue_id, safe='')}/")
        if not isinstance(data, dict):
            raise SentryError("Unexpected issue response from Sentry")
        return data

    def get_latest_event(self, org_slug: str, issue_id: str) -> dict[str, Any]:
        # Recent Sentry versions expose a dedicated latest-event endpoint. Older
        # self-hosted releases only expose the list endpoint, so fall back.
        try:
            data = self._get(f"issues/{quote(issue_id, safe='')}/events/latest/")
            if isinstance(data, dict):
                return data
        except SentryError as exc:
            if exc.status_code not in {404, 405}:
                logger.debug("latest event endpoint failed: %s", exc)
        events = self.list_issue_events(org_slug, issue_id, limit=1, full=True)
        return events[0] if events else {}

    def list_issue_events(
        self,
        org_slug: str,
        issue_id: str,
        limit: int = 8,
        full: bool = True,
        stats_period: str = "14d",
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "per_page": min(max(limit, 1), 10 if full else 100),
            "statsPeriod": stats_period,
        }
        if full:
            params["full"] = 1
        data = self._get(
            f"organizations/{quote(org_slug, safe='')}/issues/{quote(issue_id, safe='')}/events/",
            params=params,
        )
        return data if isinstance(data, list) else []

    def get_issue_commits(self, issue_id: str) -> list[dict[str, Any]]:
        try:
            data = self._get(f"issues/{quote(issue_id, safe='')}/commits/")
        except SentryError:
            return []
        return data if isinstance(data, list) else []

    def get_release(self, org_slug: str, project_slug: str, version: str) -> dict[str, Any]:
        try:
            data = self._get(
                f"projects/{quote(org_slug, safe='')}/{quote(project_slug, safe='')}/releases/{quote(version, safe='')}/"
            )
        except SentryError:
            return {}
        return data if isinstance(data, dict) else {}

    def search_issues(self, org_slug: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return self.list_issues(
            org_slug=org_slug,
            query=query or "is:unresolved",
            limit=limit,
            stats_period="30d",
            sort="date",
        )


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return response.text[:300]
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("error") or payload
        return str(detail)[:300]
    return str(payload)[:300]


def client_for(org: Any) -> SentryClient:
    from ..crypto import decrypt_secret

    token = decrypt_secret(getattr(org, "sentry_token_encrypted", None))
    if not token:
        raise SentryError("Sentry API token is not configured for this organization")
    settings = get_settings()
    return SentryClient(
        base_url=org.sentry_url,
        token=token,
        timeout=settings.sentry_request_timeout,
        verify_ssl=settings.sentry_verify_ssl,
    )


def absolute_url(base_url: str, path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith(("http://", "https://")):
        return path
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
