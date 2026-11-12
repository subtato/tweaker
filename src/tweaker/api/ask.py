from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import get_db
from ..models import AskConversation, Organization
from ..schemas import AskRequest, AskResponse, Citation
from ..services.ask import answer_question

router = APIRouter(prefix="/ask", tags=["ask"], dependencies=[Depends(require_auth)])


@router.post("", response_model=AskResponse)
def ask(payload: AskRequest, db: Session = Depends(get_db)) -> AskResponse:
    if payload.org_id:
        org = db.get(Organization, payload.org_id)
    else:
        orgs = list(db.scalars(select(Organization).limit(2)))
        org = orgs[0] if len(orgs) == 1 else None
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found or ambiguous")
    if not org.ai_enabled:
        # Local deterministic analysis is still allowed; generative calls are not.
        pass
    answer, meta = answer_question(
        db,
        org,
        payload.question,
        issue_id=payload.issue_id,
        conversation_id=payload.conversation_id,
    )
    return AskResponse(
        conversation_id=meta["conversation_id"],
        answer=answer.answer,
        citations=[Citation(**citation) for citation in meta["citations"]],
        follow_ups=meta["follow_ups"],
        provider=meta["provider"],
        model=meta["model"],
        input_tokens=meta["input_tokens"],
        output_tokens=meta["output_tokens"],
    )


@router.get("/conversations")
def list_conversations(
    org_id: str = Query(...), db: Session = Depends(get_db)
) -> dict[str, object]:
    conversations = list(
        db.scalars(
            select(AskConversation)
            .where(AskConversation.org_id == org_id)
            .order_by(AskConversation.updated_at.desc())
            .limit(25)
        )
    )
    return {
        "items": [
            {
                "id": conv.id,
                "title": conv.title,
                "updated_at": conv.updated_at,
                "messages": conv.messages or [],
            }
            for conv in conversations
        ]
    }


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    conv = db.get(AskConversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {
        "id": conv.id,
        "org_id": conv.org_id,
        "title": conv.title,
        "messages": conv.messages or [],
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
    }


@router.get("/suggestions")
def suggestions(org_id: str = Query(...), db: Session = Depends(get_db)) -> dict[str, object]:
    org = db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {
        "items": [
            "Which unresolved issue is growing fastest?",
            "What changed in the last release that could cause new errors?",
            "Find the issue most likely to be fixed by a small code change.",
            "Summarize the highest-volume error for my on-call handoff.",
        ]
    }
