from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..crypto import decrypt_secret, verify_hmac_sha256
from ..db import get_db, session_scope
from ..models import Issue, Organization, Run
from ..services.classifier import heuristic_fixability
from ..services.ingest import audit, refresh_issue, upsert_issue
from ..services.investigation import create_run
from ..services.tasks import enqueue_run, enqueue_run_sync

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])
_background_tasks: set[asyncio.Task[Any]] = set()

_ISSUE_ACTIONS = {"created", "unresolved", "assigned", "regressed"}


def _org_for_signature(
    db: Session, payload: dict[str, Any], raw: bytes, signature: str
) -> Organization | None:
    installation = payload.get("installation") or {}
    installation_id = str(installation.get("uuid") or "")
    if installation_id:
        org = db.scalar(
            select(Organization).where(Organization.sentry_installation_id == installation_id)
        )
        if org is not None:
            secret = decrypt_secret(org.webhook_secret_encrypted)
            if secret and verify_hmac_sha256(secret, raw, signature):
                return org
    for org in db.scalars(select(Organization)):
        secret = decrypt_secret(org.webhook_secret_encrypted)
        if secret and verify_hmac_sha256(secret, raw, signature):
            return org
    return None


@router.post("/sentry", status_code=202)
async def sentry_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    raw = await request.body()
    signature = request.headers.get("Sentry-Hook-Signature", "")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing Sentry-Hook-Signature")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    org = _org_for_signature(db, payload, raw, signature)
    if org is None:
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    resource = request.headers.get("Sentry-Hook-Resource", "")
    action = str(payload.get("action") or "")
    if resource != "issue" or action not in _ISSUE_ACTIONS:
        return {"status": "ignored"}
    issue_payload = (payload.get("data") or {}).get("issue")
    if not isinstance(issue_payload, dict):
        issue_payload = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if not issue_payload.get("id"):
        return {"status": "ignored"}
    issue = upsert_issue(db, org, issue_payload)
    audit(
        db,
        org.id,
        "webhook.issue",
        actor="sentry",
        target_type="issue",
        target_id=issue.id,
        metadata={"action": action},
    )
    db.commit()
    # Sentry requires a response within one second; do the expensive refresh in
    # a worker thread after acknowledging.
    task = asyncio.create_task(asyncio.to_thread(_process_issue_webhook, org.id, issue.id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"status": "accepted"}


def _process_issue_webhook(org_id: str, issue_id: str) -> None:
    try:
        with session_scope() as session:
            org = session.get(Organization, org_id)
            issue = session.get(Issue, issue_id)
            if org is None or issue is None:
                return
            if org.sentry_token_encrypted:
                try:
                    issue = refresh_issue(session, org, issue)
                except Exception as exc:
                    logger.warning("Webhook refresh failed: %s", exc)
            assessment = heuristic_fixability(issue, issue.latest_event or {})
            issue.fixability_score = assessment.score
            issue.fixability_reason = assessment.reason
            issue.fixability_signals = assessment.signals
            issue.scanned_at = datetime.now(UTC)
            session.commit()
            if not org.auto_investigate or assessment.score < org.fixability_threshold:
                return
            existing = session.scalar(
                select(Run).where(
                    Run.issue_id == issue.id, Run.status.in_(["queued", "running", "succeeded"])
                )
            )
            if existing is not None:
                return
            run = create_run(
                session,
                org=org,
                issue=issue,
                kind="autofix" if org.auto_fix else "investigate",
                trigger="webhook",
                publish=bool(org.auto_fix),
                actor="sentry",
            )
        enqueue_run_sync(run.id)
    except Exception:
        logger.exception("Webhook processing failed for issue %s", issue_id)


def _slack_verify(raw: bytes, headers: dict[str, str]) -> bool:
    settings = get_settings()
    secret = settings.slack_signing_secret
    if not secret:
        return False
    timestamp = headers.get("x-slack-request-timestamp", "")
    signature = headers.get("x-slack-signature", "")
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except ValueError:
        return False
    base = b"v0:" + timestamp.encode() + b":" + raw
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/slack")
async def slack_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    raw = await request.body()
    if not _slack_verify(raw, {k.lower(): v for k, v in request.headers.items()}):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = json.loads(raw)
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge")}
        return {"ok": True}
    form = parse_qs(raw.decode("utf-8"))
    text = (form.get("text") or [""])[0].strip()
    org = db.scalar(select(Organization).limit(1))
    if org is None:
        return {"response_type": "ephemeral", "text": "Tweaker has no organization configured yet."}
    issue = _find_issue(db, org, text)
    if issue is None:
        return {
            "response_type": "ephemeral",
            "text": "I couldn't match that to a Sentry issue. Paste an issue URL or a short ID like `BACKEND-1A2B`.",
        }
    run = create_run(
        db,
        org=org,
        issue=issue,
        kind="autofix" if org.auto_fix else "investigate",
        trigger="slack",
        publish=bool(org.auto_fix),
        actor="slack",
    )
    await enqueue_run(run.id)
    return {
        "response_type": "ephemeral",
        "text": f"Investigating {issue.short_id or issue.sentry_id} — {issue.title}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Tweaker is investigating* <{issue.permalink}|{issue.short_id}> — {issue.title}",
                },
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Run `{run.id[:8]}` · you'll see the result in the Tweaker dashboard."}
                ],
            },
        ],
    }


def _find_issue(db: Session, org: Organization, text: str) -> Issue | None:
    if not text:
        return None
    parts = text.replace("/", " ").split()
    candidates = [part for part in parts if part]
    for candidate in reversed(candidates):
        issue = db.scalar(
            select(Issue).where(
                Issue.org_id == org.id,
                (Issue.sentry_id == candidate) | (Issue.short_id == candidate),
            )
        )
        if issue is not None:
            return issue
    # A bare short ID is often uppercase with a dash.
    for candidate in candidates:
        upper = candidate.upper()
        issue = db.scalar(
            select(Issue).where(Issue.org_id == org.id, Issue.short_id == upper)
        )
        if issue is not None:
            return issue
    return None
