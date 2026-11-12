from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy import select

from ..db import session_scope
from ..models import SeerSupergroup

logger = logging.getLogger(__name__)


def _error_type(issue: dict[str, Any]) -> str:
    metadata = issue.get("metadata") if isinstance(issue.get("metadata"), dict) else {}
    value = metadata.get("type") or metadata.get("value")
    if value:
        return str(value)[:300]
    title = str(issue.get("title") or "")
    return title.split(":", 1)[0][:300]


def _code_area(issue: dict[str, Any]) -> str:
    events = issue.get("events") if isinstance(issue.get("events"), list) else []
    for event in events:
        if not isinstance(event, dict):
            continue
        for entry in event.get("entries") or []:
            if not isinstance(entry, dict) or entry.get("type") != "exception":
                continue
            values = (entry.get("data") or {}).get("values") or []
            for value in values:
                frames = (value.get("stacktrace") or {}).get("frames") or []
                for frame in reversed(frames):
                    if not isinstance(frame, dict) or not frame.get("inApp"):
                        continue
                    filename = str(frame.get("filename") or "")
                    if filename:
                        return "/".join(filename.split("/")[:-1]) or filename
    metadata = issue.get("metadata") if isinstance(issue.get("metadata"), dict) else {}
    filename = str(metadata.get("filename") or "")
    return "/".join(filename.split("/")[:-1]) or filename


def _fingerprint(error_type: str, code_area: str) -> str:
    return hashlib.sha256(f"{error_type}|{code_area}".encode()).hexdigest()[:32]


def cluster_lightweight(payload: dict[str, Any]) -> dict[str, Any]:
    org_id = int(payload.get("organization_id") or 0)
    issue = payload.get("issue") if isinstance(payload.get("issue"), dict) else {}
    group_id = issue.get("id") or payload.get("group_id")
    project_id = payload.get("project_id")
    error_type = _error_type(issue)
    code_area = _code_area(issue)
    fingerprint = _fingerprint(error_type, code_area)
    with session_scope() as session:
        sg = session.scalar(
            select(SeerSupergroup).where(
                SeerSupergroup.org_id == org_id,
                SeerSupergroup.fingerprint == fingerprint,
            )
        )
        if sg is None:
            sg = SeerSupergroup(
                org_id=org_id,
                fingerprint=fingerprint,
                title=str(issue.get("title") or error_type or "Related issues")[:500],
                summary=f"{error_type} in {code_area}" if code_area else error_type,
                error_type=error_type,
                code_area=code_area,
                group_ids=[],
                project_ids=[],
            )
            session.add(sg)
            session.flush()
        group_ids = list(sg.group_ids or [])
        if group_id is not None and int(group_id) not in group_ids:
            group_ids.append(int(group_id))
        project_ids = list(sg.project_ids or [])
        if project_id is not None and int(project_id) not in project_ids:
            project_ids.append(int(project_id))
        sg.group_ids = group_ids
        sg.project_ids = project_ids
        session.commit()
        return {"supergroup_id": sg.id}


def get_supergroup(org_id: int, supergroup_id: int) -> dict[str, Any] | None:
    with session_scope() as session:
        sg = session.get(SeerSupergroup, supergroup_id)
        if sg is None or sg.org_id != org_id:
            return None
        return _to_dict(sg)


def get_supergroups_by_group_ids(org_id: int, group_ids: list[int]) -> dict[str, Any]:
    with session_scope() as session:
        rows = list(session.scalars(select(SeerSupergroup).where(SeerSupergroup.org_id == org_id)))
        data = []
        wanted = set(group_ids)
        for sg in rows:
            matched = [gid for gid in (sg.group_ids or []) if gid in wanted]
            if matched:
                item = _to_dict(sg)
                item["group_ids"] = matched
                data.append(item)
        return {"data": data}


def _to_dict(sg: SeerSupergroup) -> dict[str, Any]:
    return {
        "id": sg.id,
        "title": sg.title,
        "summary": sg.summary,
        "error_type": sg.error_type,
        "code_area": sg.code_area,
        "group_ids": list(sg.group_ids or []),
        "project_ids": list(sg.project_ids or []),
        "created_at": sg.created_at.isoformat() if sg.created_at else "",
        "updated_at": sg.updated_at.isoformat() if sg.updated_at else "",
    }
