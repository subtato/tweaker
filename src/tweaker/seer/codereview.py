from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from ..config import get_settings
from ..db import session_scope
from ..metrics import counter, observe
from ..models import SeerCodeReview
from ..services.llm import ProviderError, get_provider

logger = logging.getLogger(__name__)


def create_code_review(payload: dict[str, Any], org_id: int) -> SeerCodeReview:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    repo = data.get("repo") if isinstance(data.get("repo"), dict) else {}
    with session_scope() as session:
        item = SeerCodeReview(
            org_id=org_id,
            provider=str(repo.get("provider") or ""),
            owner=str(repo.get("owner") or ""),
            name=str(repo.get("name") or ""),
            pr_id=str(data.get("pr_id") or ""),
            status="processing",
            payload=payload,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return item


def close_code_review(payload: dict[str, Any], org_id: int) -> None:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    repo = data.get("repo") if isinstance(data.get("repo"), dict) else {}
    pr_id = str(data.get("pr_id") or "")
    with session_scope() as session:
        items = list(
            session.query(SeerCodeReview)
            .filter(
                SeerCodeReview.org_id == org_id,
                SeerCodeReview.owner == str(repo.get("owner") or ""),
                SeerCodeReview.name == str(repo.get("name") or ""),
                SeerCodeReview.pr_id == pr_id,
            )
            .all()
        )
        for item in items:
            item.status = "closed"
        session.commit()


def execute_code_review(review_id: int) -> None:
    started = time.monotonic()
    with session_scope() as session:
        item = session.get(SeerCodeReview, review_id)
        if item is None:
            return
        payload = item.payload or {}
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        repo = data.get("repo") if isinstance(data.get("repo"), dict) else {}
        pr_id = str(data.get("pr_id") or item.pr_id)
        provider = str(repo.get("provider") or item.provider)
        owner = str(repo.get("owner") or item.owner)
        name = str(repo.get("name") or item.name)
        try:
            diff = _fetch_diff(provider, owner, name, pr_id)
            if not diff:
                raise RuntimeError("Could not fetch the pull request diff")
            review = _review_diff(diff)
            item.summary = review.get("summary")
            item.comments = review.get("comments") or []
            item.status = "completed"
            _post_review(provider, owner, name, pr_id, review)
        except Exception as exc:
            logger.warning("Code review %s failed: %s", review_id, exc)
            item.status = "error"
            item.error = str(exc)[:4000]
        counter("tweaker_seer_runs_total", kind="code_review", status=item.status)
        observe(
            "tweaker_seer_run_duration_seconds",
            time.monotonic() - started,
            kind="code_review",
        )
        session.commit()


def _fetch_diff(provider: str, owner: str, name: str, pr_id: str) -> str:
    settings = get_settings()
    if "gitlab" in provider.lower():
        token = settings.gitlab_token
        if not token:
            raise RuntimeError("TWEAKER_GITLAB_TOKEN is not configured")
        host = urlparse(settings.gitlab_base_url).hostname or "gitlab.com"
        project = quote(f"{owner}/{name}", safe="")
        response = httpx.get(
            f"https://{host}/api/v4/projects/{project}/merge_requests/{pr_id}/changes",
            headers={"PRIVATE-TOKEN": token},
            timeout=30,
        )
        response.raise_for_status()
        changes = response.json().get("changes") or []
        return "\n\n".join(
            f"--- {change.get('old_path')}\n+++ {change.get('new_path')}\n{change.get('diff')}"
            for change in changes
        )
    token = settings.github_token
    if not token:
        raise RuntimeError("TWEAKER_GITHUB_TOKEN is not configured")
    host = urlparse(settings.github_base_url).hostname or "github.com"
    api_base = "https://api.github.com" if host in {"github.com", "www.github.com"} else f"https://{host}/api/v3"
    response = httpx.get(
        f"{api_base}/repos/{owner}/{name}/pulls/{pr_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3.diff",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def _review_diff(diff: str) -> dict[str, Any]:
    provider = get_provider()
    prompt = (
        "Review this pull request diff for correctness bugs. Return JSON only:\n"
        '{"summary": "short review summary", "comments": ['
        '{"path": "file", "line": 12, "severity": "low|medium|high|critical", '
        '"body": "specific actionable issue"}]}\n'
        "Only report concrete bugs introduced by the diff. Do not report style nits.\n\n"
        f"<untrusted_diff>\n{diff[:80_000]}\n</untrusted_diff>"
    )
    try:
        text, _ = provider.complete_text(
            system="You are a precise, conservative code reviewer.",
            prompt=prompt,
            max_tokens=3000,
        )
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                data.setdefault("comments", [])
                data.setdefault("summary", "")
                return data
    except (ProviderError, json.JSONDecodeError) as exc:
        logger.info("Code review model failed: %s", exc)
    return {"summary": "Automated review could not be generated.", "comments": []}


def _post_review(
    provider: str, owner: str, name: str, pr_id: str, review: dict[str, Any]
) -> None:
    settings = get_settings()
    comments = review.get("comments") or []
    body = str(review.get("summary") or "Tweaker code review")
    if comments:
        body += "\n\n" + "\n".join(
            f"- `{item.get('path')}:{item.get('line')}` ({item.get('severity', 'medium')}): {item.get('body')}"
            for item in comments[:50]
            if isinstance(item, dict)
        )
    if "gitlab" in provider.lower():
        token = settings.gitlab_token
        if not token:
            return
        host = urlparse(settings.gitlab_base_url).hostname or "gitlab.com"
        project = quote(f"{owner}/{name}", safe="")
        response = httpx.post(
            f"https://{host}/api/v4/projects/{project}/merge_requests/{pr_id}/notes",
            headers={"PRIVATE-TOKEN": token},
            json={"body": body},
            timeout=30,
        )
        response.raise_for_status()
        return
    token = settings.github_token
    if not token:
        return
    host = urlparse(settings.github_base_url).hostname or "github.com"
    api_base = "https://api.github.com" if host in {"github.com", "www.github.com"} else f"https://{host}/api/v3"
    response = httpx.post(
        f"{api_base}/repos/{owner}/{name}/issues/{pr_id}/comments",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"body": body},
        timeout=30,
    )
    response.raise_for_status()
