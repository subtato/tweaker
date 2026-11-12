from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..metrics import counter, observe
from ..models import SeerCodeReview
from ..services.llm import ProviderError, get_provider
from ..services.scm import github_api_base, gitlab_api_base, token_for
from .diff import unified_diff_to_patches

logger = logging.getLogger(__name__)

CHECK_NAME = "Tweaker code review"


def create_code_review(payload: dict[str, Any], org_id: int) -> SeerCodeReview:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    repo = data.get("repo") if isinstance(data.get("repo"), dict) else {}
    original = _original_review(payload)
    if original is not None:
        with session_scope() as session:
            rerun = SeerCodeReview(
                org_id=original.org_id,
                provider=original.provider,
                owner=original.owner,
                name=original.name,
                pr_id=original.pr_id,
                base_sha=original.base_sha,
                status="processing",
                payload=original.payload,
            )
            session.add(rerun)
            session.commit()
            session.refresh(rerun)
            return rerun
    with session_scope() as session:
        item = SeerCodeReview(
            org_id=org_id,
            provider=str(repo.get("provider") or ""),
            owner=str(repo.get("owner") or ""),
            name=str(repo.get("name") or ""),
            pr_id=str(data.get("pr_id") or ""),
            base_sha=str(repo.get("base_commit_sha") or "") or None,
            status="processing",
            payload=payload,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return item


def _original_review(payload: dict[str, Any]) -> SeerCodeReview | None:
    """Resolve ``original_run_id`` from a GitHub check-run rerun request.

    Sentry forwards the ``external_id`` of the original check run, which Tweaker
    sets to the numeric review id. Older payloads may carry it inside ``data``.
    """
    raw = payload.get("original_run_id")
    if raw is None and isinstance(payload.get("data"), dict):
        raw = payload["data"].get("original_run_id")
    if raw is None:
        return None
    text = str(raw)
    with session_scope() as session:
        item = session.get(SeerCodeReview, int(text)) if text.isdigit() else None
        if item is None:
            item = session.scalar(
                select(SeerCodeReview).where(SeerCodeReview.check_run_id == text)
            )
        if item is None:
            return None
        session.expunge(item)
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
            item.finished_at = datetime.now(UTC)
            _complete_check_run(
                item,
                conclusion="cancelled",
                summary="Pull request closed; code review cancelled.",
                comments=[],
            )
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
        item.started_at = datetime.now(UTC)
        try:
            metadata = _fetch_pr_metadata(provider, owner, name, pr_id)
            head_sha = str(metadata.get("head_sha") or item.head_sha or "")
            base_sha = str(metadata.get("base_sha") or item.base_sha or "")
            item.head_sha = head_sha or None
            item.base_sha = base_sha or None
            _start_check_run(item, provider, owner, name, head_sha)
            if not item.check_run_url:
                item.check_run_url = metadata.get("url")
            session.commit()

            diff = _fetch_diff(provider, owner, name, pr_id)
            if not diff:
                raise RuntimeError("Could not fetch the pull request diff")
            review = _review_diff(
                diff,
                title=str(metadata.get("title") or ""),
                description=str(metadata.get("description") or ""),
            )
            comments = _valid_comments(diff, review.get("comments") or [])
            item.summary = str(review.get("summary") or "")
            item.comments = comments
            item.comment_count = len(comments)
            item.conclusion = _conclusion(comments)
            item.review_url = _post_review(
                item,
                provider,
                owner,
                name,
                pr_id,
                head_sha,
                base_sha,
                item.summary,
                comments,
            ) or metadata.get("url")
            item.status = "completed"
            _complete_check_run(
                item,
                conclusion=item.conclusion,
                summary=item.summary,
                comments=comments,
            )
        except Exception as exc:
            logger.warning("Code review %s failed: %s", review_id, exc)
            item.status = "error"
            item.error = str(exc)[:4000]
            _complete_check_run(
                item,
                conclusion="failure",
                summary=f"Code review failed: {exc}"[:2000],
                comments=[],
            )
        item.finished_at = datetime.now(UTC)
        counter("tweaker_seer_runs_total", kind="code_review", status=item.status)
        observe(
            "tweaker_seer_run_duration_seconds",
            time.monotonic() - started,
            kind="code_review",
        )
        session.commit()


def _fetch_pr_metadata(provider: str, owner: str, name: str, pr_id: str) -> dict[str, Any]:
    settings = get_settings()
    if "gitlab" in provider.lower():
        token = token_for(provider, settings)
        if not token:
            raise RuntimeError("TWEAKER_GITLAB_TOKEN is not configured")
        project = quote(f"{owner}/{name}", safe="")
        response = httpx.get(
            f"{_gitlab_api_base()}/projects/{project}/merge_requests/{pr_id}",
            headers={"PRIVATE-TOKEN": token},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        refs = data.get("diff_refs") or {}
        return {
            "head_sha": data.get("sha") or refs.get("head_sha"),
            "base_sha": refs.get("base_sha"),
            "title": data.get("title"),
            "description": data.get("description"),
            "url": data.get("web_url"),
            "state": data.get("state"),
        }
    token = token_for(provider, settings)
    if not token:
        raise RuntimeError("TWEAKER_GITHUB_TOKEN is not configured")
    response = httpx.get(
        f"{_github_api_base()}/repos/{owner}/{name}/pulls/{pr_id}",
        headers=_github_headers(token, accept="application/vnd.github+json"),
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return {
        "head_sha": (data.get("head") or {}).get("sha"),
        "base_sha": (data.get("base") or {}).get("sha"),
        "title": data.get("title"),
        "description": data.get("body"),
        "url": data.get("html_url"),
        "state": data.get("state"),
    }


def _fetch_diff(provider: str, owner: str, name: str, pr_id: str) -> str:
    settings = get_settings()
    if "gitlab" in provider.lower():
        token = token_for(provider, settings)
        if not token:
            raise RuntimeError("TWEAKER_GITLAB_TOKEN is not configured")
        project = quote(f"{owner}/{name}", safe="")
        response = httpx.get(
            f"{_gitlab_api_base()}/projects/{project}/merge_requests/{pr_id}/changes",
            headers={"PRIVATE-TOKEN": token},
            timeout=30,
        )
        response.raise_for_status()
        changes = response.json().get("changes") or []
        return "\n\n".join(
            f"--- {change.get('old_path')}\n+++ {change.get('new_path')}\n{change.get('diff')}"
            for change in changes
        )
    token = token_for(provider, settings)
    if not token:
        raise RuntimeError("TWEAKER_GITHUB_TOKEN is not configured")
    response = httpx.get(
        f"{_github_api_base()}/repos/{owner}/{name}/pulls/{pr_id}",
        headers=_github_headers(token, accept="application/vnd.github.v3.diff"),
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def _review_diff(diff: str, *, title: str = "", description: str = "") -> dict[str, Any]:
    provider = get_provider()
    prompt = (
        "Review this pull request diff for correctness bugs. Return JSON only:\n"
        '{"summary": "short review summary", "comments": ['
        '{"path": "file", "line": 12, "severity": "low|medium|high|critical", '
        '"body": "specific actionable issue"}]}\n'
        "Only report concrete bugs introduced by the diff. Do not report style nits. "
        "Use target-file line numbers from the diff.\n\n"
        f"Title: {title[:500]}\nDescription: {description[:3000]}\n\n"
        f"<untrusted_diff>\n{diff[:100_000]}\n</untrusted_diff>"
    )
    try:
        text, _ = provider.complete_text(
            system="You are a precise, conservative code reviewer.",
            prompt=prompt,
            max_tokens=4000,
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


def _valid_comments(diff: str, comments: list[Any]) -> list[dict[str, Any]]:
    patches = unified_diff_to_patches(diff, "pr")
    valid_lines: dict[str, set[int]] = {}
    for patch in patches:
        file_patch = patch.get("patch") or {}
        path = str(file_patch.get("path") or "")
        lines: set[int] = set()
        for hunk in file_patch.get("hunks") or []:
            for line in hunk.get("lines") or []:
                if line.get("target_line_no") is not None and line.get("line_type") in {"+", " "}:
                    lines.add(int(line["target_line_no"]))
        valid_lines[path] = lines
    result: list[dict[str, Any]] = []
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        path = str(comment.get("path") or "")
        try:
            line = int(comment.get("line") or 0)
        except (TypeError, ValueError):
            continue
        if path not in valid_lines or line not in valid_lines[path]:
            continue
        result.append(
            {
                "path": path,
                "line": line,
                "severity": str(comment.get("severity") or "medium"),
                "body": str(comment.get("body") or "")[:4000],
            }
        )
        if len(result) >= 50:
            break
    return result


def _conclusion(comments: list[dict[str, Any]]) -> str:
    severities = {str(comment.get("severity") or "").lower() for comment in comments}
    if "critical" in severities or "high" in severities:
        return "failure"
    if comments:
        return "neutral"
    return "success"


def _start_check_run(
    item: SeerCodeReview, provider: str, owner: str, name: str, head_sha: str
) -> None:
    if not head_sha:
        return
    settings = get_settings()
    try:
        if "gitlab" in provider.lower():
            token = token_for(provider, settings)
            if not token:
                return
            project = quote(f"{owner}/{name}", safe="")
            response = httpx.post(
                f"{_gitlab_api_base()}/projects/{project}/statuses/{head_sha}",
                headers={"PRIVATE-TOKEN": token},
                json={
                    "state": "running",
                    "name": CHECK_NAME,
                    "description": "Reviewing pull request…",
                },
                timeout=30,
            )
            response.raise_for_status()
            return
        token = token_for(provider, settings)
        if not token:
            return
        response = httpx.post(
            f"{_github_api_base()}/repos/{owner}/{name}/check-runs",
            headers=_github_headers(token),
            json={
                "name": CHECK_NAME,
                "head_sha": head_sha,
                "external_id": str(item.id),
                "status": "in_progress",
                "started_at": datetime.now(UTC).isoformat(),
                "output": {
                    "title": "Tweaker code review",
                    "summary": "Reviewing the pull request…",
                },
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        item.check_run_id = str(data.get("id") or "") or None
        item.check_run_url = data.get("html_url")
    except Exception as exc:
        logger.info("Could not start code-review check for %s: %s", owner, exc)


def _complete_check_run(
    item: SeerCodeReview,
    *,
    conclusion: str,
    summary: str,
    comments: list[dict[str, Any]],
) -> None:
    settings = get_settings()
    provider = item.provider
    owner, name = item.owner, item.name
    try:
        if "gitlab" in provider.lower():
            token = token_for(provider, settings)
            if not token or not item.head_sha:
                return
            project = quote(f"{owner}/{name}", safe="")
            state = "canceled" if conclusion == "cancelled" else ("failed" if conclusion == "failure" else "success")
            response = httpx.post(
                f"{_gitlab_api_base()}/projects/{project}/statuses/{item.head_sha}",
                headers={"PRIVATE-TOKEN": token},
                json={
                    "state": state,
                    "name": CHECK_NAME,
                    "description": summary[:140] or "Code review complete",
                },
                timeout=30,
            )
            response.raise_for_status()
            return
        token = token_for(provider, settings)
        if not token or not item.check_run_id:
            return
        annotations = [
            {
                "path": comment["path"],
                "start_line": comment["line"],
                "end_line": comment["line"],
                "annotation_level": _annotation_level(comment.get("severity")),
                "message": comment["body"][:65535],
                "title": f"{str(comment.get('severity') or 'medium').title()} severity",
            }
            for comment in comments[:50]
        ]
        response = httpx.patch(
            f"{_github_api_base()}/repos/{owner}/{name}/check-runs/{item.check_run_id}",
            headers=_github_headers(token),
            json={
                "status": "completed",
                "conclusion": conclusion,
                "completed_at": datetime.now(UTC).isoformat(),
                "output": {
                    "title": "Tweaker code review",
                    "summary": summary[:65000] or "Code review complete.",
                    "text": _summary_markdown(summary, comments)[:65000],
                    "annotations": annotations,
                },
            },
            timeout=30,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.info("Could not complete code-review check for %s: %s", owner, exc)


def _post_review(
    item: SeerCodeReview,
    provider: str,
    owner: str,
    name: str,
    pr_id: str,
    head_sha: str,
    base_sha: str,
    summary: str,
    comments: list[dict[str, Any]],
) -> str | None:
    settings = get_settings()
    if "gitlab" in provider.lower():
        token = token_for(provider, settings)
        if not token:
            return None
        project = quote(f"{owner}/{name}", safe="")
        for comment in comments:
            try:
                response = httpx.post(
                    f"{_gitlab_api_base()}/projects/{project}/merge_requests/{pr_id}/discussions",
                    headers={"PRIVATE-TOKEN": token},
                    json={
                        "body": _comment_markdown(comment),
                        "position": {
                            "position_type": "text",
                            "base_sha": base_sha,
                            "start_sha": base_sha,
                            "head_sha": head_sha,
                            "new_path": comment["path"],
                            "new_line": comment["line"],
                        },
                    },
                    timeout=30,
                )
                response.raise_for_status()
            except Exception as exc:
                logger.info("Could not post inline GitLab comment: %s", exc)
        response = httpx.post(
            f"{_gitlab_api_base()}/projects/{project}/merge_requests/{pr_id}/notes",
            headers={"PRIVATE-TOKEN": token},
            json={"body": _summary_markdown(summary, comments)},
            timeout=30,
        )
        response.raise_for_status()
        return None
    token = token_for(provider, settings)
    if not token:
        return None
    url = f"{_github_api_base()}/repos/{owner}/{name}/pulls/{pr_id}/reviews"
    payload = {
        "commit_id": head_sha,
        "body": _summary_markdown(summary, comments),
        "event": "COMMENT",
        "comments": [
            {
                "path": comment["path"],
                "line": comment["line"],
                "side": "RIGHT",
                "body": _comment_markdown(comment),
            }
            for comment in comments
        ],
    }
    response = httpx.post(url, headers=_github_headers(token), json=payload, timeout=30)
    if response.status_code < 400:
        data = response.json()
        return data.get("html_url")
    logger.info("GitHub review API failed (%s); falling back to issue comment", response.status_code)
    fallback = httpx.post(
        f"{_github_api_base()}/repos/{owner}/{name}/issues/{pr_id}/comments",
        headers=_github_headers(token),
        json={"body": _summary_markdown(summary, comments)},
        timeout=30,
    )
    fallback.raise_for_status()
    return fallback.json().get("html_url")


def _annotation_level(severity: Any) -> str:
    value = str(severity or "").lower()
    if value in {"critical", "high"}:
        return "failure"
    if value == "medium":
        return "warning"
    return "notice"


def _comment_markdown(comment: dict[str, Any]) -> str:
    severity = str(comment.get("severity") or "medium").upper()
    return f"**[{severity}]** {comment.get('body')}"


def _summary_markdown(summary: str, comments: list[dict[str, Any]]) -> str:
    lines = [summary or "Tweaker code review", ""]
    if comments:
        lines.append(f"Found {len(comments)} issue(s):")
        lines.extend(
            f"- `{comment['path']}:{comment['line']}` — {_comment_markdown(comment)}"
            for comment in comments
        )
    else:
        lines.append("No correctness bugs found in this diff.")
    lines.append("")
    lines.append("_Generated by Tweaker. Review before merging._")
    return "\n".join(lines)


def _github_headers(token: str, *, accept: str = "application/vnd.github+json") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _github_api_base() -> str:
    return github_api_base(get_settings().github_base_url)


def _gitlab_api_base() -> str:
    return gitlab_api_base(get_settings().gitlab_base_url)
