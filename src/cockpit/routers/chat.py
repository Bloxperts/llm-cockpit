"""Chat router (UC-04) + shared `/api/models` picker endpoint.

Per UC-04 functional spec:

    POST   /api/chat                    → 201 {conversation_id, mode: "chat"}
    GET    /api/chat                    → list own chat conversations
    GET    /api/chat/{id}               → full conversation + messages
    POST   /api/chat/{id}/stream        → SSE via stream_reply()
    POST   /api/chat/{id}/regenerate    → SSE (re-run last user turn)
    PATCH  /api/chat/{id}               → { title?, model?, system_prompt? }
    DELETE /api/chat/{id}               → 204
    GET    /api/models?tag=chat         → LLMChat.list_models() ∩ tag filter

Every route depends on `current_user_must_be_settled` (UC-09) + the role
gate via `require_role`. UC-05 (`routers/code.py`) reuses the same
`stream_reply` service and adds the `code` role gate + the default
system prompt.

The `/api/models` endpoint lives here because it's shared — `chat.py` is
included exactly once in `main.py`. UC-05 imports it via the module
globals if needed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from cockpit.config import Settings
from cockpit.deps import get_chat_factory, get_session, get_settings
from cockpit.models import Conversation, Message, ModelTag, User
from cockpit.routers.auth import (
    current_user_must_be_settled,
    require_role_settled,
)
from cockpit.schemas import (
    ConversationCreateRequest,
    ConversationCreateResponse,
    ConversationDetail,
    ConversationPatchRequest,
    ConversationSummary,
    MessagePayload,
    ModelPickerEntry,
    StreamRequest,
)
from cockpit.services.chat import (
    conversation_message_count,
    get_conversation_for_user,
    list_conversations,
    stream_reply,
)

log = logging.getLogger(__name__)

router = APIRouter()

CHAT_MODE = "chat"


# --- /api/models picker (shared between /chat and /code) ------------------


def _allowed_tags_for(tag: str) -> set[str]:
    """`tag=chat` accepts `chat` + `both`; `tag=code` accepts `code` + `both`."""
    if tag == "chat":
        return {"chat", "both"}
    if tag == "code":
        return {"code", "both"}
    return set()


@router.get(
    "/api/models",
    response_model=list[ModelPickerEntry],
    summary="Filtered model picker (used by /chat and /code).",
    include_in_schema=True,
)
async def list_models_for_picker(
    request: Request,
    tag: str = Query(..., pattern="^(chat|code)$"),
    user: User = Depends(current_user_must_be_settled),
    db: Session = Depends(get_session),
    chat_factory=Depends(get_chat_factory),
) -> list[ModelPickerEntry]:
    """Return models from `LLMChat.list_models()` joined with `model_tags`,
    filtered to entries whose tag matches `?tag=`. Entries with `tag='both'`
    show in either picker.
    """
    allowed = _allowed_tags_for(tag)
    if not allowed:
        raise HTTPException(422, detail={"detail": "invalid_tag", "tag": tag})

    # Chat factory returns an LLMChat-conforming object; close it after use.
    chat = chat_factory(request.app.state.settings.ollama_url)
    try:
        ollama_models = await chat.list_models()
    finally:
        aclose = getattr(chat, "aclose", None)
        if aclose is not None:
            await aclose()

    tags_by_model: dict[str, str] = {
        t.model: t.tag
        for t in db.execute(
            select(ModelTag).where(
                ModelTag.model.in_([m.name for m in ollama_models])
            )
        ).scalars()
    }

    out: list[ModelPickerEntry] = []
    for m in ollama_models:
        model_tag = tags_by_model.get(m.name)
        if model_tag is None:
            # Untagged models default to `chat` per ADR-004 §3 heuristic, but
            # we won't surface them in the code picker.
            model_tag = "chat"
        if model_tag in allowed:
            out.append(
                ModelPickerEntry(name=m.name, tag=model_tag, size_bytes=m.size_bytes)
            )
    return out


# --- helpers shared with UC-05's code router ------------------------------


def _serialize_message(m: Message) -> MessagePayload:
    return MessagePayload(
        id=m.id,
        role=m.role,
        content=m.content,
        model=m.model,
        usage_in=m.usage_in,
        usage_out=m.usage_out,
        gen_tps=m.gen_tps,
        latency_ms=m.latency_ms,
        ts=m.ts.isoformat() if m.ts else "",
        error=m.error,
    )


def _serialize_conversation_summary(
    session: Session, c: Conversation
) -> ConversationSummary:
    return ConversationSummary(
        id=c.id,
        mode=c.mode,
        title=c.title,
        model=c.model,
        system_prompt=c.system_prompt,
        created_at=c.created_at.isoformat() if c.created_at else "",
        updated_at=c.updated_at.isoformat() if c.updated_at else "",
        message_count=conversation_message_count(session, c.id),
    )


def _serialize_conversation_detail(
    session: Session, c: Conversation
) -> ConversationDetail:
    msgs = list(
        session.execute(
            select(Message)
            .where(Message.conversation_id == c.id)
            .order_by(Message.ts, Message.id)
        ).scalars()
    )
    return ConversationDetail(
        id=c.id,
        mode=c.mode,
        title=c.title,
        model=c.model,
        system_prompt=c.system_prompt,
        created_at=c.created_at.isoformat() if c.created_at else "",
        updated_at=c.updated_at.isoformat() if c.updated_at else "",
        messages=[_serialize_message(m) for m in msgs],
    )


def _resolve_default_system_prompt(session: Session, *, mode: str) -> str | None:
    """UC-05: code mode pre-fills `system_prompt` from `settings.code_default_system_prompt`,
    falling back to the bundled `default_config/code_default_system_prompt.md`.
    UC-04: chat mode has no default — None.
    """
    if mode != "code":
        return None
    from cockpit.models import Setting

    row = session.execute(
        select(Setting).where(Setting.key == "code_default_system_prompt")
    ).scalar_one_or_none()
    if row is not None and row.value:
        return row.value
    # Bundled fallback.
    from importlib import resources

    try:
        return (
            resources.files("cockpit")
            .joinpath("default_config/code_default_system_prompt.md")
            .read_text(encoding="utf-8")
        )
    except Exception:  # pragma: no cover — package data missing
        return (
            "You are an expert pair programmer. Be terse, produce correct code, "
            "and prefer working examples over explanations."
        )


def _create_conversation(
    session: Session,
    *,
    user: User,
    mode: str,
    body: ConversationCreateRequest,
) -> Conversation:
    system_prompt = body.system_prompt or _resolve_default_system_prompt(
        session, mode=mode
    )
    c = Conversation(
        user_id=user.id,
        mode=mode,
        model=body.model,
        title=body.title,
        system_prompt=system_prompt,
    )
    session.add(c)
    session.flush()
    return c


def _patch_conversation(
    session: Session,
    *,
    conversation: Conversation,
    body: ConversationPatchRequest,
) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if body.title is not None:
        conversation.title = body.title
        changes["title"] = body.title
    if body.system_prompt is not None:
        conversation.system_prompt = body.system_prompt
        changes["system_prompt"] = body.system_prompt
    # Per UC-04 §model picker: switching the model mid-conversation is
    # tracked on subsequent messages; we record it on the conversation row
    # for "current selection" purposes.
    if body.model is not None:
        conversation.model = body.model
        changes["model"] = body.model
    if changes:
        conversation.updated_at = datetime.now(timezone.utc)
    session.flush()
    return changes


def _delete_conversation(session: Session, conversation: Conversation) -> None:
    session.execute(
        delete(Message).where(Message.conversation_id == conversation.id)
    )
    session.delete(conversation)
    session.flush()


async def _stream_response(
    *,
    user: User,
    conversation: Conversation,
    user_content: str,
    request: Request,
    settings: Settings,
    chat_factory,
) -> EventSourceResponse:
    """Build the SSE response. The actual generator owns its own session +
    LLMChat adapter so it doesn't compete with FastAPI's per-request
    dependency machinery for the streaming lifetime.
    """
    session_factory = request.app.state.session_factory
    ollama_url = settings.ollama_url

    async def gen() -> AsyncIterator[dict]:
        chat = chat_factory(ollama_url)
        try:
            with session_factory() as session:
                # Re-load the conversation in this fresh session.
                conv = session.get(Conversation, conversation.id)
                if conv is None:
                    yield {
                        "event": "error",
                        "data": json.dumps(
                            {"code": "conversation_not_found", "message": ""}
                        ),
                    }
                    return
                async for event in stream_reply(
                    conversation=conv,
                    user_content=user_content,
                    llm=chat,
                    session=session,
                    settings=settings,
                ):
                    yield event
        finally:
            aclose = getattr(chat, "aclose", None)
            if aclose is not None:
                await aclose()

    return EventSourceResponse(gen())


# --- Conversation CRUD (chat) --------------------------------------------


@router.post(
    "/api/chat",
    response_model=ConversationCreateResponse,
    status_code=201,
    summary="Create a new chat conversation.",
)
async def create_chat_conversation(
    body: ConversationCreateRequest,
    user: User = Depends(require_role_settled("chat")),
    db: Session = Depends(get_session),
) -> ConversationCreateResponse:
    conv = _create_conversation(db, user=user, mode=CHAT_MODE, body=body)
    db.commit()
    return ConversationCreateResponse(conversation_id=conv.id, mode=conv.mode)


@router.get(
    "/api/chat",
    response_model=list[ConversationSummary],
    summary="List the user's chat conversations.",
)
async def list_chat_conversations(
    user: User = Depends(require_role_settled("chat")),
    db: Session = Depends(get_session),
) -> list[ConversationSummary]:
    convs = list_conversations(db, user_id=user.id, mode=CHAT_MODE)
    return [_serialize_conversation_summary(db, c) for c in convs]


@router.get(
    "/api/chat/{conversation_id}",
    response_model=ConversationDetail,
    summary="Fetch a chat conversation + its messages.",
)
async def get_chat_conversation(
    conversation_id: int,
    user: User = Depends(require_role_settled("chat")),
    db: Session = Depends(get_session),
) -> ConversationDetail:
    conv = get_conversation_for_user(
        db, conversation_id=conversation_id, user_id=user.id, mode=CHAT_MODE
    )
    if conv is None:
        raise HTTPException(404, detail="conversation_not_found")
    return _serialize_conversation_detail(db, conv)


@router.post(
    "/api/chat/{conversation_id}/stream",
    summary="Stream an assistant reply for a chat conversation.",
)
async def stream_chat_reply(
    conversation_id: int,
    body: StreamRequest,
    request: Request,
    user: User = Depends(require_role_settled("chat")),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    chat_factory=Depends(get_chat_factory),
) -> EventSourceResponse:
    conv = get_conversation_for_user(
        db, conversation_id=conversation_id, user_id=user.id, mode=CHAT_MODE
    )
    if conv is None:
        raise HTTPException(404, detail="conversation_not_found")
    return await _stream_response(
        user=user,
        conversation=conv,
        user_content=body.content,
        request=request,
        settings=settings,
        chat_factory=chat_factory,
    )


@router.post(
    "/api/chat/{conversation_id}/regenerate",
    summary="Re-run the last user turn through the same model + system prompt.",
)
async def regenerate_chat_reply(
    conversation_id: int,
    request: Request,
    user: User = Depends(require_role_settled("chat")),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    chat_factory=Depends(get_chat_factory),
) -> EventSourceResponse:
    conv = get_conversation_for_user(
        db, conversation_id=conversation_id, user_id=user.id, mode=CHAT_MODE
    )
    if conv is None:
        raise HTTPException(404, detail="conversation_not_found")
    # Find the last user message — that's what we re-run.
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
    "/api/chat/{conversation_id}",
    summary="Update a chat conversation's title / model / system_prompt.",
)
async def patch_chat_conversation(
    conversation_id: int,
    body: ConversationPatchRequest,
    user: User = Depends(require_role_settled("chat")),
    db: Session = Depends(get_session),
) -> dict:
    conv = get_conversation_for_user(
        db, conversation_id=conversation_id, user_id=user.id, mode=CHAT_MODE
    )
    if conv is None:
        raise HTTPException(404, detail="conversation_not_found")
    changes = _patch_conversation(db, conversation=conv, body=body)
    db.commit()
    return {"updated": changes}


@router.delete(
    "/api/chat/{conversation_id}",
    summary="Delete a chat conversation + its messages.",
    status_code=204,
)
async def delete_chat_conversation(
    conversation_id: int,
    user: User = Depends(require_role_settled("chat")),
    db: Session = Depends(get_session),
) -> Response:
    conv = get_conversation_for_user(
        db, conversation_id=conversation_id, user_id=user.id, mode=CHAT_MODE
    )
    if conv is None:
        raise HTTPException(404, detail="conversation_not_found")
    _delete_conversation(db, conv)
    db.commit()
    return Response(status_code=204)
