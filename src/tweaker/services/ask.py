from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import AskConversation, Issue, Organization
from .llm import ProviderError, get_provider
from .llm.prompts import ASK_SYSTEM, build_ask_prompt, context_to_text
from .llm.schema import AskAnswer
from .redaction import compact_event
from .sentry import client_for

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "what", "which", "why", "how",
    "does", "do", "did", "for", "from", "with", "that", "this", "there", "have",
    "has", "had", "can", "could", "should", "would", "about", "into", "our", "we",
    "me", "my", "show", "tell", "find", "issue", "issues", "error", "errors",
    "sentry", "seer", "please", "any", "all", "and", "or", "of", "in", "on",
}


def _terms(question: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9_./:-]{3,}", question)
    seen: list[str] = []
    for word in words:
        lowered = word.lower()
        if lowered in _STOPWORDS or lowered in seen:
            continue
        seen.append(lowered)
    return seen[:8]


def retrieve_context(
    session: Session,
    org: Organization,
    question: str,
    *,
    issue_id: str | None = None,
    limit: int = 8,
) -> tuple[list[Issue], dict[str, dict[str, Any]]]:
    issues: list[Issue] = []
    if issue_id:
        issue = session.get(Issue, issue_id)
        if issue and issue.org_id == org.id:
            issues.append(issue)
    if not issues:
        clauses = []
        for term in _terms(question):
            pattern = f"%{term}%"
            clauses.extend(
                [
                    Issue.title.ilike(pattern),
                    Issue.culprit.ilike(pattern),
                    Issue.short_id.ilike(pattern),
                ]
            )
        if clauses:
            issues = list(
                session.scalars(
                    select(Issue)
                    .where(Issue.org_id == org.id, or_(*clauses))
                    .order_by(Issue.last_seen.desc().nullslast())
                    .limit(limit)
                )
            )
    if not issues:
        issues = list(
            session.scalars(
                select(Issue)
                .where(Issue.org_id == org.id, Issue.status == "unresolved")
                .order_by(Issue.last_seen.desc().nullslast())
                .limit(limit)
            )
        )

    # If the local cache is empty, try a live Sentry search once.
    if not issues and org.sentry_token_encrypted:
        try:
            with client_for(org) as client:
                payloads = client.search_issues(org.sentry_org_slug, question, limit=limit)
            from .ingest import upsert_issue

            issues = [upsert_issue(session, org, payload, commit=False) for payload in payloads]
            session.commit()
        except Exception as exc:
            logger.info("Live Sentry search failed: %s", exc)

    events = {issue.id: issue.latest_event or {} for issue in issues}
    return issues, events


def answer_question(
    session: Session,
    org: Organization,
    question: str,
    *,
    issue_id: str | None = None,
    conversation_id: str | None = None,
) -> tuple[AskAnswer, dict[str, Any]]:
    issues, events = retrieve_context(session, org, question, issue_id=issue_id)
    context = context_to_text(issues, events)
    focused_issue = issues[0] if issue_id and issues else None
    conversation = session.get(AskConversation, conversation_id) if conversation_id else None
    if conversation is not None and conversation.org_id != org.id:
        conversation = None
    history = list(conversation.messages or []) if conversation else []

    provider = get_provider(org)
    answer: AskAnswer
    usage = None
    if not issues:
        answer = AskAnswer(
            answer=(
                "I couldn't find matching issues in the local cache. Sync recent issues from Sentry "
                "or try a narrower query such as `is:unresolved release:1.2.3`."
            ),
            citations=[],
            follow_ups=["Sync recent issues", "Show the most recent unresolved issues"],
            confidence=0.2,
        )
    else:
        try:
            result = provider.complete_structured(
                system=ASK_SYSTEM,
                prompt=build_ask_prompt(
                    question,
                    context,
                    issue=focused_issue,
                    history=history,
                ),
                schema=AskAnswer,
                max_tokens=3000,
            )
            answer = result.value
            usage = result.usage
        except ProviderError as exc:
            logger.warning("Ask provider failed: %s", exc)
            answer = AskAnswer(
                answer=(
                    f"The model request failed ({exc}). Here is the matching Sentry context: "
                    f"{len(issues)} issue(s) matched. The most recent is "
                    f"{issues[0].short_id} — {issues[0].title}."
                ),
                citations=[issue.short_id for issue in issues[:5]],
                follow_ups=["Retry with a local model", "Investigate the most recent issue"],
                confidence=0.3,
            )

    # Resolve citations against retrieved issues, and add any short IDs the
    # model mentioned that we know about.
    by_short = {issue.short_id: issue for issue in issues if issue.short_id}
    citation_ids: list[str] = []
    for short_id in answer.citations:
        if short_id in by_short and short_id not in citation_ids:
            citation_ids.append(short_id)
    for short_id in by_short:
        if short_id and short_id in answer.answer and short_id not in citation_ids:
            citation_ids.append(short_id)
    if not citation_ids:
        citation_ids = [issue.short_id for issue in issues[:3] if issue.short_id]

    citations = [
        {
            "type": "issue",
            "id": by_short[short_id].id,
            "title": f"{short_id} — {by_short[short_id].title}",
            "url": by_short[short_id].permalink,
        }
        for short_id in citation_ids
        if short_id in by_short
    ]

    if conversation is None:
        conversation = AskConversation(
            org_id=org.id,
            title=question[:120],
            messages=[],
        )
        session.add(conversation)
    conversation.messages = [
        *(conversation.messages or []),
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer.answer, "citations": citation_ids},
    ]
    session.commit()
    meta = {
        "conversation_id": conversation.id,
        "citations": citations,
        "follow_ups": answer.follow_ups,
        "provider": provider.name,
        "model": provider.model,
        "input_tokens": usage.input_tokens if usage else 0,
        "output_tokens": usage.output_tokens if usage else 0,
        "issues": [
            {
                "id": issue.id,
                "short_id": issue.short_id,
                "title": issue.title,
                "level": issue.level,
                "last_seen": issue.last_seen,
                "fixability_score": issue.fixability_score,
                "latest_event": compact_event(issue.latest_event or {}, max_chars=2000),
            }
            for issue in issues[:limit_issues(issues)]
        ],
    }
    return answer, meta


def limit_issues(issues: list[Issue], limit: int = 8) -> int:
    return min(len(issues), limit)
