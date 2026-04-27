"""Code router (UC-05).

Per UC-05 functional spec: identical shell to UC-04's chat router, with
two differences:

1. **Role gate** — `require_role_settled("code")`. `chat` users get 403.
2. **Default system prompt** — pre-filled from `settings.code_default_system_prompt`,
   falling back to the bundled `default_config/code_default_system_prompt.md`.

The shared `/api/models` picker endpoint lives in `routers/chat.py` (see
docstring there); the picker filters by `?tag=code`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from cockpit.config import Settings
from cockpit.deps import get_chat_factory, get_session, get_settings
from cockpit.models import Message, User
from cockpit.routers.auth import require_role_settled
from cockpit.routers.chat import (
    _create_conversation,
    _delete_conversation,
    _options_from_stream_request,
    _patch_conversation,
    _serialize_conversation_detail,
    _serialize_conversation_summary,
    _stream_response,
)
from cockpit.schemas import (
    ConversationCreateRequest,
    ConversationCreateResponse,
    ConversationDetail,
    ConversationPatchRequest,
    ConversationSummary,
    StreamRequest,
)
from cockpit.services.chat import (
    get_conversation_for_user,
    list_conversations,
)

log = logging.getLogger(__name__)

router = APIRouter()
CODE_MODE = "code"


@router.post(
    "/api/code",
    response_model=ConversationCreateResponse,
    status_code=201,
    summary="Create a new code conversation.",
)
async def create_code_conversation(
    body: ConversationCreateRequest,
    user: User = Depends(require_role_settled("code")),
    db: Session = Depends(get_session),
) -> ConversationCreateResponse:
    conv = _create_conversation(db, user=user, mode=CODE_MODE, body=body)
    db.commit()
    return ConversationCreateResponse(conversation_id=conv.id, mode=conv.mode)


@router.get(
    "/api/code",
    response_model=list[ConversationSummary],
    summary="List the user's code conversations.",
)
async def list_code_conversations(
    user: User = Depends(require_role_settled("code")),
    db: Session = Depends(get_session),
) -> list[ConversationSummary]:
    convs = list_conversations(db, user_id=user.id, mode=CODE_MODE)
    return [_serialize_conversation_summary(db, c) for c in convs]


@router.get(
    "/api/code/{conversation_id}",
    response_model=ConversationDetail,
    summary="Fetch a code conversation + its messages.",
)
async def get_code_conversation(
    conversation_id: int,
    user: User = Depends(require_role_settled("code")),
    db: Session = Depends(get_session),
) -> ConversationDetail:
    conv = get_conversation_for_user(
        db, conversation_id=conversation_id, user_id=user.id, mode=CODE_MODE
    )
    if conv is None:
        raise HTTPException(404, detail="conversation_not_found")
    return _serialize_conversation_detail(db, conv)


@router.post(
    "/api/code/{conversation_id}/stream",
    summary="Stream an assistant reply for a code conversation.",
)
async def stream_code_reply(
    conversation_id: int,
    body: StreamRequest,
    request: Request,
    user: User = Depends(require_role_settled("code")),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    chat_factory=Depends(get_chat_factory),
) -> EventSourceResponse:
    conv = get_conversation_for_user(
        db, conversation_id=conversation_id, user_id=user.id, mode=CODE_MODE
    )
    if conv is None:
        raise HTTPException(404, detail="conversation_not_found")
    options = _options_from_stream_request(body)
    return await _stream_response(
        user=user,
        conversation=conv,
        user_content=body.content,
        request=request,
        settings=settings,
        chat_factory=chat_factory,
        options=options,
    )


@router.post(
    "/api/code/{conversation_id}/regenerate",
    summary="Re-run the last user turn of a code conversation.",
)
async def regenerate_code_reply(
    conversation_id: int,
    request: Request,
    user: User = Depends(require_role_settled("code")),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    chat_factory=Depends(get_chat_factory),
) -> EventSourceResponse:
    conv = get_conversation_for_user(
        db, conversation_id=conversation_id, user_id=user.id, mode=CODE_MODE
    )
    if conv is None:
        raise HTTPException(404, detail="conversation_not_found")
    last_user = (
        db.execute(
            select(Message)
            .where(Message.conversation_id == conv.id, Message.role == "user")
            .order_by(Message.ts.desc(), Message.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if last_user is None:
        raise HTTPException(400, detail="no_prior_user_message")
    return await _stream_response(
        user=user,
        conversation=conv,
        user_content=last_user.content,
        request=request,
        settings=settings,
        chat_factory=chat_factory,
    )


@router.patch(
    "/api/code/{conversation_id}",
    summary="Update a code conversation's title / model / system_prompt.",
)
async def patch_code_conversation(
    conversation_id: int,
    body: ConversationPatchRequest,
    user: User = Depends(require_role_settled("code")),
    db: Session = Depends(get_session),
) -> dict:
    conv = get_conversation_for_user(
        db, conversation_id=conversation_id, user_id=user.id, mode=CODE_MODE
    )
    if conv is None:
        raise HTTPException(404, detail="conversation_not_found")
    changes = _patch_conversation(db, conversation=conv, body=body)
    db.commit()
    return {"updated": changes}


@router.delete(
    "/api/code/{conversation_id}",
    summary="Delete a code conversation.",
    status_code=204,
)
async def delete_code_conversation(
    conversation_id: int,
    user: User = Depends(require_role_settled("code")),
    db: Session = Depends(get_session),
) -> Response:
    conv = get_conversation_for_user(
        db, conversation_id=conversation_id, user_id=user.id, mode=CODE_MODE
    )
    if conv is None:
        raise HTTPException(404, detail="conversation_not_found")
    _delete_conversation(db, conv)
    db.commit()
    return Response(status_code=204)
