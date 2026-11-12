from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..crypto import encrypt_secret
from ..db import get_db
from ..models import Organization, Repository
from ..schemas import RepositoryCreate, RepositoryOut, RepositoryUpdate
from ..services.codebase import CodebaseError, RepositoryWorkspace
from ..services.ingest import audit

router = APIRouter(tags=["repositories"], dependencies=[Depends(require_auth)])


def _out(repo: Repository) -> RepositoryOut:
    return RepositoryOut(
        id=repo.id,
        org_id=repo.org_id,
        name=repo.name,
        provider=repo.provider,
        url=repo.url,
        default_branch=repo.default_branch,
        local_path=repo.local_path,
        has_access_token=bool(repo.access_token_encrypted),
        project_slugs=repo.project_slugs or [],
        test_command=repo.test_command,
        enabled=repo.enabled,
        created_at=repo.created_at,
        updated_at=repo.updated_at,
    )


@router.get("/orgs/{org_id}/repos", response_model=list[RepositoryOut])
def list_repos(org_id: str, db: Session = Depends(get_db)) -> list[RepositoryOut]:
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    repos = db.scalars(
        select(Repository).where(Repository.org_id == org_id).order_by(Repository.created_at)
    ).all()
    return [_out(repo) for repo in repos]


@router.post("/orgs/{org_id}/repos", response_model=RepositoryOut, status_code=201)
def create_repo(
    org_id: str, payload: RepositoryCreate, db: Session = Depends(get_db)
) -> RepositoryOut:
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    repo = Repository(
        org_id=org_id,
        name=payload.name,
        provider=payload.provider,
        url=payload.url.rstrip("/"),
        default_branch=payload.default_branch,
        local_path=payload.local_path,
        access_token_encrypted=encrypt_secret(payload.access_token),
        project_slugs=payload.project_slugs,
        test_command=payload.test_command,
        enabled=payload.enabled,
    )
    db.add(repo)
    db.flush()
    if org.default_repo_id is None:
        org.default_repo_id = repo.id
    audit(db, org_id, "repo.created", actor="user", target_type="repo", target_id=repo.id)
    db.commit()
    db.refresh(repo)
    return _out(repo)


@router.patch("/repos/{repo_id}", response_model=RepositoryOut)
def update_repo(
    repo_id: str, payload: RepositoryUpdate, db: Session = Depends(get_db)
) -> RepositoryOut:
    repo = db.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    data = payload.model_dump(exclude_unset=True)
    if "access_token" in data:
        token = data.pop("access_token")
        repo.access_token_encrypted = encrypt_secret(token) if token else None
    for key, value in data.items():
        setattr(repo, key, value)
    audit(db, repo.org_id, "repo.updated", actor="user", target_type="repo", target_id=repo.id)
    db.commit()
    db.refresh(repo)
    return _out(repo)


@router.delete("/repos/{repo_id}", status_code=204)
def delete_repo(repo_id: str, db: Session = Depends(get_db)) -> None:
    repo = db.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    org = db.get(Organization, repo.org_id)
    if org and org.default_repo_id == repo.id:
        org.default_repo_id = None
    audit(db, repo.org_id, "repo.deleted", actor="user", target_type="repo", target_id=repo.id)
    db.delete(repo)
    db.commit()


@router.post("/repos/{repo_id}/check")
def check_repo(repo_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    repo = db.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    try:
        workspace = RepositoryWorkspace(repo)
        root = workspace.ensure()
        return {"ok": True, "message": "Repository is accessible.", "path": str(root)}
    except CodebaseError as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}


@router.post("/repos/{repo_id}/index")
def index_repo(repo_id: str, full: bool = False, db: Session = Depends(get_db)) -> dict[str, object]:
    repo = db.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    from ..services.code_index import index_repository

    try:
        result = index_repository(repo, full=full)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audit(db, repo.org_id, "repo.indexed", actor="user", target_type="repo", target_id=repo.id)
    db.commit()
    return result


@router.post("/repos/index")
def index_all_repos(full: bool = False) -> dict[str, object]:
    from ..services.code_index import index_all

    return index_all(full=full)


@router.get("/repos/{repo_id}/index")
def repo_index_stats(repo_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    repo = db.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    from ..services.code_index import index_stats

    return index_stats(db, repo.id)
