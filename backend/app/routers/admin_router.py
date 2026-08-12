"""
Admin-only endpoint for reviewing agent behavior across every user --
tool calls, retrieved sources, and guardrail flags per conversation turn.

This is the "review AI system logs, traces, prompts, outputs, tool
calls, and telemetry to identify anomalies" capability. A normal user
only ever sees their own conversations (chat_router.py's
GET /chat/conversations); this is the same underlying data with the
per-user scoping removed, gated by is_admin instead of just being open
to anyone authenticated -- see deps.get_current_admin_user.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import desc
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.deps import get_current_admin_user
from app.models import Conversation, User
from app.schemas import AdminConversationResponse

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/traces", response_model=list[AdminConversationResponse])
def list_traces(
    limit: int = 50,
    flagged_only: bool = False,
    _admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Most recent conversations across all users, newest first. Pass
    flagged_only=true to see only conversations where the output
    guardrail actually flagged something -- the view you'd reach for
    first during an incident review, rather than scrolling everything."""
    conversations = (
        db.query(Conversation)
        .options(joinedload(Conversation.user), joinedload(Conversation.messages))
        .order_by(desc(Conversation.created_at))
        .limit(limit)
        .all()
    )

    results = []
    for c in conversations:
        if flagged_only and not any(m.guardrail_flags for m in c.messages):
            continue
        results.append(
            {
                "id": c.id,
                "title": c.title,
                "created_at": c.created_at,
                "user_email": c.user.email,
                "messages": c.messages,
            }
        )
    return results
