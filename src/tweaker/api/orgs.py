from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..crypto import encrypt_secret
from ..db import get_db
from ..models import Organization
from ..schemas import (
    ConnectionTestOut,
    OrganizationCreate,
    OrganizationOut,
    OrganizationUpdate,
)
from ..services.ingest import audit, sync_issues
from ..services.sentry import SentryError, client_for

router = APIRouter(prefix="/orgs", tags=["organizations"], dependencies=[Depends(require_auth)])


def _out(org: Organization) -> OrganizationOut:
    return OrganizationOut(
        id=org.id,
        slug=org.slug,
        name=org.name,
        sentry_url=org.sentry_url,
        sentry_org_slug=org.sentry_org_slug,
        sentry_installation_id=org.sentry_installation_id,
        has_sentry_token=bool(org.sentry_token_encrypted),
        has_webhook_secret=bool(org.webhook_secret_encrypted),
        ai_enabled=org.ai_enabled,
        auto_investigate=org.auto_investigate,
        auto_fix=org.auto_fix,
        fixability_threshold=org.fixability_threshold,
        default_repo_id=org.default_repo_id,
        created_at=org.created_at,
        updated_at=org.updated_at,
    )


@router.get("", response_model=list[OrganizationOut])
def list_orgs(db: Session = Depends(get_db)) -> list[OrganizationOut]:
    orgs = db.scalars(select(Organization).order_by(Organization.created_at)).all()
    return [_out(org) for org in orgs]


@router.post("", response_model=OrganizationOut, status_code=201)
def create_org(payload: OrganizationCreate, db: Session = Depends(get_db)) -> OrganizationOut:
    if db.scalar(select(Organization).where(Organization.slug == payload.slug)):
        raise HTTPException(status_code=409, detail="An organization with that slug already exists")
    org = Organization(
        slug=payload.slug,
        name=payload.name,
        sentry_url=payload.sentry_url.rstrip("/"),
        sentry_org_slug=payload.sentry_org_slug,
        sentry_installation_id=payload.sentry_installation_id,
        sentry_token_encrypted=encrypt_secret(payload.sentry_token),
        webhook_secret_encrypted=encrypt_secret(payload.webhook_secret),
        ai_enabled=payload.ai_enabled,
        auto_investigate=payload.auto_investigate,
        auto_fix=payload.auto_fix,
        fixability_threshold=payload.fixability_threshold,
    )
    db.add(org)
    db.flush()
    audit(db, org.id, "org.created", actor="user", target_type="org", target_id=org.id)
    db.commit()
    db.refresh(org)
    return _out(org)


@router.get("/{org_id}", response_model=OrganizationOut)
def get_org(org_id: str, db: Session = Depends(get_db)) -> OrganizationOut:
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return _out(org)


@router.patch("/{org_id}", response_model=OrganizationOut)
def update_org(
    org_id: str, payload: OrganizationUpdate, db: Session = Depends(get_db)
) -> OrganizationOut:
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    data = payload.model_dump(exclude_unset=True)
    for field in ("sentry_token", "webhook_secret"):
        if field in data:
            value = data.pop(field)
            if value == "":
                setattr(org, f"{field}_encrypted", None)
            elif value is not None:
                setattr(org, f"{field}_encrypted", encrypt_secret(value))
    for key, value in data.items():
        setattr(org, key, value)
    audit(db, org.id, "org.updated", actor="user", target_type="org", target_id=org.id)
    db.commit()
    db.refresh(org)
    return _out(org)


@router.post("/{org_id}/test-sentry", response_model=ConnectionTestOut)
def test_sentry(org_id: str, db: Session = Depends(get_db)) -> ConnectionTestOut:
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not org.sentry_token_encrypted:
        return ConnectionTestOut(ok=False, message="Add a Sentry API token first.")
    try:
        with client_for(org) as client:
            if not org.sentry_org_slug:
                client.test_connection()
                return ConnectionTestOut(
                    ok=True,
                    message="Connected to Sentry. Set an organization slug to list projects.",
                    organization=None,
                    projects=[],
                )
            organization = client.get_organization(org.sentry_org_slug)
            projects = client.list_projects(org.sentry_org_slug)
        return ConnectionTestOut(
            ok=True,
            message=f"Connected to {organization.get('name') or org.sentry_org_slug}.",
            organization=str(organization.get("slug") or org.sentry_org_slug),
            projects=[
                {"id": p.get("id"), "slug": p.get("slug"), "name": p.get("name")}
                for p in projects[:100]
            ],
        )
    except (SentryError, ValueError) as exc:
        return ConnectionTestOut(ok=False, message=str(exc))


@router.get("/{org_id}/projects")
def list_projects(org_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not org.sentry_token_encrypted:
        raise HTTPException(status_code=400, detail="Sentry token is not configured")
    try:
        with client_for(org) as client:
            projects = client.list_projects(org.sentry_org_slug)
    except (SentryError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "items": [
            {"id": p.get("id"), "slug": p.get("slug"), "name": p.get("name"), "platform": p.get("platform")}
            for p in projects
        ]
    }


@router.post("/{org_id}/sync")
def sync_org(
    org_id: str,
    project_slug: str | None = Query(default=None),
    query: str = Query(default="is:unresolved"),
    limit: int = Query(default=25, ge=1, le=100),
    with_events: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not org.sentry_token_encrypted:
        raise HTTPException(status_code=400, detail="Sentry token is not configured")
    try:
        issues = sync_issues(
            db,
            org,
            project_slug=project_slug,
            query=query,
            limit=limit,
            with_events=with_events,
        )
    except (SentryError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "synced": len(issues),
        "issues": [{"id": issue.id, "short_id": issue.short_id, "title": issue.title} for issue in issues],
    }
