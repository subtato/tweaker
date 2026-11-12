"""Model Context Protocol server for Tweaker.

Run with ``tweaker-mcp`` (stdio). Configure it in Claude Code, Cursor, or any
MCP-compatible agent with:

    TWEAKER_URL=http://localhost:8080
    TWEAKER_AUTH_TOKEN=<service token>
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

try:  # optional dependency
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    FastMCP = None  # type: ignore[assignment]

BASE_URL = os.environ.get("TWEAKER_URL", "http://localhost:8080").rstrip("/")
TOKEN = os.environ.get("TWEAKER_AUTH_TOKEN") or os.environ.get("TWEAKER_API_TOKEN")


def _client() -> httpx.Client:
    headers = {"Accept": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    return httpx.Client(base_url=BASE_URL, headers=headers, timeout=120)


def _request(method: str, path: str, **kwargs: Any) -> Any:
    with _client() as client:
        response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except Exception:
            detail = response.text[:500]
        raise RuntimeError(f"Tweaker API {response.status_code}: {detail}")
    if response.status_code == 204:
        return None
    return response.json()


def _org_id() -> str | None:
    return os.environ.get("TWEAKER_ORG_ID")


if FastMCP is not None:
    mcp = FastMCP(
        "Tweaker",
        instructions=(
            "Tweaker is an AI debugging service for self-hosted Sentry. Use these tools to "
            "inspect production issues, fetch stack traces, start investigations, read proposed "
            "patches, and ask grounded questions about telemetry."
        ),
    )

    @mcp.tool()
    def list_issues(status: str = "unresolved", search: str = "", limit: int = 10) -> str:
        """List Sentry issues cached in Tweaker, newest first."""
        params: dict[str, Any] = {"status": status, "per_page": min(max(limit, 1), 50)}
        if search:
            params["search"] = search
        if _org_id():
            params["org_id"] = _org_id()
        data = _request("GET", "/api/issues", params=params)
        return json.dumps(data, indent=2, default=str)

    @mcp.tool()
    def get_issue(issue_id: str) -> str:
        """Get an issue with its latest event, tags, and investigation history."""
        return json.dumps(_request("GET", f"/api/issues/{issue_id}"), indent=2, default=str)

    @mcp.tool()
    def investigate_issue(issue_id: str, autofix: bool = False) -> str:
        """Start a root-cause investigation. Set autofix=true to request a patch."""
        payload = {"mode": "autofix" if autofix else "investigate", "publish": False}
        return json.dumps(
            _request("POST", f"/api/issues/{issue_id}/investigate", json=payload),
            indent=2,
            default=str,
        )

    @mcp.tool()
    def get_run(run_id: str) -> str:
        """Read an investigation's status, timeline, root cause, and patch."""
        return json.dumps(_request("GET", f"/api/runs/{run_id}"), indent=2, default=str)

    @mcp.tool()
    def ask_seer(question: str, issue_id: str | None = None) -> str:
        """Ask a natural-language question grounded in cached Sentry telemetry."""
        payload: dict[str, Any] = {"question": question}
        if _org_id():
            payload["org_id"] = _org_id()
        if issue_id:
            payload["issue_id"] = issue_id
        return json.dumps(_request("POST", "/api/ask", json=payload), indent=2, default=str)

    @mcp.tool()
    def list_seer_runs(limit: int = 10) -> str:
        """List Seer-contract runs (Autofix, Agent, assisted query)."""
        data = _request("GET", "/api/seer-runs", params={"per_page": min(max(limit, 1), 50)})
        return json.dumps(data, indent=2, default=str)

    @mcp.tool()
    def get_seer_run(run_id: int) -> str:
        """Get a Seer-contract run with blocks, artifacts, and PR state."""
        return json.dumps(_request("GET", f"/api/seer-runs/{run_id}"), indent=2, default=str)

    @mcp.tool()
    def get_usage(org_id: str) -> str:
        """Get token, cost, run usage and the active quota for an organization."""
        return json.dumps(_request("GET", "/api/usage", params={"org_id": org_id}), indent=2, default=str)

    @mcp.tool()
    def get_patch(run_id: str) -> str:
        """Return the unified diff proposed by an investigation."""
        with _client() as client:
            response = client.get(f"/api/runs/{run_id}/patch")
        if response.status_code >= 400:
            raise RuntimeError(f"Tweaker API {response.status_code}: {response.text[:500]}")
        return response.text


def main() -> None:
    if FastMCP is None:
        raise SystemExit(
            "The MCP server requires the optional dependency: pip install 'tweaker[mcp]'"
        )
    mcp.run()


if __name__ == "__main__":
    main()
