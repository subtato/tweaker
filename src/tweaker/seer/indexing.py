from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..models import CodeIndexEntry
from ..services.code_index import index_stats, index_workspace
from .repos import RepoDefinition, RepoManager
from .rpc import SentryRPCClient, SentryRPCError

logger = logging.getLogger(__name__)


def repo_key(repo: RepoDefinition) -> str:
    return f"{repo.provider}:{repo.full_name}"


def _repo_from_map_key(key: str) -> RepoDefinition | None:
    value = str(key or "").strip()
    if not value:
        return None
    provider = "github"
    if ":" in value and "/" in value.split(":", 1)[1]:
        provider, value = value.split(":", 1)
    if value.count("/") < 1:
        return None
    owner, name = value.rsplit("/", 1)
    if not owner or not name:
        return None
    return RepoDefinition(provider=provider, owner=owner, name=name)


def _demo_repos() -> list[RepoDefinition]:
    try:
        mapping = json.loads(get_settings().repo_map or "{}")
    except json.JSONDecodeError:
        return []
    if not isinstance(mapping, dict):
        return []
    repos: list[RepoDefinition] = []
    seen: set[str] = set()
    for key in mapping:
        repo = _repo_from_map_key(str(key))
        if repo is None or repo_key(repo) in seen:
            continue
        seen.add(repo_key(repo))
        repos.append(repo)
    return repos


def resolve_org_repos(
    org_id: int,
    viewer_context: str | None,
    *,
    project_ids: list[int] | None = None,
) -> list[RepoDefinition]:
    """Resolve the repositories connected to an organization's projects."""
    if get_settings().demo_mode:
        return _demo_repos()
    repos: list[RepoDefinition] = []
    seen: set[str] = set()
    try:
        with SentryRPCClient(viewer_context=viewer_context) as rpc:
            targets = list(project_ids or [])
            if not targets:
                projects = rpc.get_organization_projects(org_id)
                for project in projects.get("projects") or []:
                    if not isinstance(project, dict):
                        continue
                    try:
                        targets.append(int(project.get("id")))
                    except (TypeError, ValueError):
                        continue
            for project_id in targets[:50]:
                preference = rpc.get_project_preferences(org_id, project_id)
                for item in preference.get("repositories") or []:
                    if not isinstance(item, dict):
                        continue
                    repo = RepoDefinition.from_wire(item)
                    key = repo_key(repo)
                    if key not in seen:
                        seen.add(key)
                        repos.append(repo)
    except (SentryRPCError, OSError, ValueError) as exc:
        logger.info("Could not resolve repositories for org %s: %s", org_id, exc)
    return repos


def index_repo_definitions(
    repos: list[RepoDefinition],
    *,
    viewer_context: str | None = None,
    full: bool = False,
) -> list[dict[str, Any]]:
    manager = RepoManager(viewer_context=viewer_context)
    results: list[dict[str, Any]] = []
    for repo in repos:
        key = repo_key(repo)
        try:
            workspace = manager.ensure(repo)
            result = index_workspace(workspace, key, full=full)
            result["repo"] = repo.full_name
            result["provider"] = repo.provider
            results.append(result)
        except Exception as exc:
            logger.warning("Could not index %s: %s", key, exc)
            results.append(
                {
                    "repo_id": key,
                    "repo": repo.full_name,
                    "provider": repo.provider,
                    "error": str(exc)[:1000],
                }
            )
    return results


def index_org_repos(
    org_id: int,
    viewer_context: str | None,
    *,
    project_ids: list[int] | None = None,
    repos: list[RepoDefinition] | None = None,
    full: bool = False,
) -> dict[str, Any]:
    targets = repos or resolve_org_repos(
        org_id, viewer_context, project_ids=project_ids
    )
    results = index_repo_definitions(
        targets, viewer_context=viewer_context, full=full
    )
    return {
        "org_id": org_id,
        "repositories": results,
        "indexed": sum(int(item.get("indexed") or 0) for item in results),
        "failed": sum(1 for item in results if item.get("error")),
    }


def export_org_indexes(
    org_id: int,
    viewer_context: str | None,
    *,
    project_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Export the local code index for the org's connected repositories."""
    repos = resolve_org_repos(org_id, viewer_context, project_ids=project_ids)
    tables: dict[str, Any] = {}
    total_files = 0
    remaining = 3000
    for repo in repos:
        if remaining <= 0:
            break
        key = repo_key(repo)
        with session_scope() as session:
            rows = list(
                session.scalars(
                    select(CodeIndexEntry)
                    .where(CodeIndexEntry.repo_id == key)
                    .order_by(CodeIndexEntry.path)
                    .limit(min(2000, remaining))
                )
            )
            stats = index_stats(session, key)
        files = [
            {
                "path": row.path,
                "language": row.language,
                "symbols": (row.symbols or "").split()[:100],
                "line_count": row.line_count,
                "mtime": row.mtime,
                "content": (row.content or "")[:8_000],
            }
            for row in rows
        ]
        total_files += len(files)
        remaining -= len(files)
        tables[key] = {
            "repo_name": repo.full_name,
            "provider": repo.provider,
            "files": files,
            "stats": stats,
        }
    return {
        "org_id": org_id,
        "version": 1,
        "tables": tables,
        "repos": [
            {"repo_id": repo_key(repo), "repo_name": repo.full_name, "provider": repo.provider}
            for repo in repos
        ],
        "file_count": total_files,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
