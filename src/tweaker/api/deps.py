from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Issue, Organization

DbSession = Annotated[Session, Depends(get_db)]


def get_org(
    session: DbSession,
    org_id: str | None = Query(default=None),
    org_slug: str | None = Query(default=None),
) -> Organization:
    if org_id:
        org = session.get(Organization, org_id)
    elif org_slug:
        org = session.scalar(select(Organization).where(Organization.slug == org_slug))
    else:
        orgs = list(session.scalars(select(Organization).limit(2)))
        org = orgs[0] if len(orgs) == 1 else None
        if len(orgs) > 1:
            raise HTTPException(status_code=400, detail="org_id is required when multiple organizations exist")
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


def get_issue(session: DbSession, issue_id: str) -> Issue:
    issue = session.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    return issue
