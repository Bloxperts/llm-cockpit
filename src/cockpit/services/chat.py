"""Chat service — shared backend for UC-04 (chat) and UC-05 (code).

`stream_reply` is the one place that:
- Appends the user message to the conversation.
- Calls `LLMChat.chat_stream(...)` with the right messages list.
- Re-emits Ollama's NDJSON chunks as cockpit-side SSE events.
- Persists the assistant message (full content, usage_*, latency_ms,
  gen_tps, error) — including partial saves on disconnect or upstream
  abort.

The `mode` argument is implicit in the conversation object — both routers
call this same function. UC-04 = mode 'chat'; UC-05 = mode 'code'.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cockpit.config import Settings
from cockpit.models import Conversation, Message
from cockpit.ports.llm_chat import (
    LLMChat,
    OllamaModelNotFound,
    OllamaResponseError,
    OllamaStreamAbortedError,
    OllamaUnreachableError,
)

log = logging.getLogger(__name__)


def _build_message_history(
    *,
    conversation: Conversation,
    db_messages: list[Message],
    user_content: str,
) -> list[dict[str, Any]]:
    """Construct the `messages` list passed to `LLMChat.chat_stream`.

    Order:
        1. system_prompt (if any) as `{"role": "system", ...}`
        2. all prior messages from the DB in `ts` order
        3. the new user content as the last `{"role": "user", ...}`
    """
    out: list[dict[str, Any]] = []
    if conversation.system_prompt:
        out.append({"role": "system", "content": conversation.system_prompt})
    for m in db_messages:
        if m.role in ("user", "assistant"):
            out.append({"role": m.role, "content": m.content})
    out.append({"role": "user", "content": user_content})
    return out


def _ordered_messages(session: Session, conversation_id: int) -> list[Message]:
    return list(
        session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.ts, Message.id)
        )
        .scalars()
    )


def _persist_user_message(
    session: Session,
    conversation: Conversation,
    user_content: str,
) -> Message:
    msg = Message(
        conversation_id=conversation.id,
        role="user",
        content=user_content,
        model=conversation.model,
    )
    session.add(msg)
    session.flush()
    conversation.updated_at = datetime.now(timezone.utc)
    session.flush()
    return msg


def _persist_assistant_message(
    session: Session,
    conversation: Conversation,
    *,
    content: str,
    model: str | None,
    usage_in: int | None,
    usage_out: int | None,
    gen_tps: float | None,
    latency_ms: int | None,
    error: str | None,
) -> Message:
    msg = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=content,
        model=model,
        usage_in=usage_in,
        usage_out=usage_out,
        gen_tps=gen_tps,
        latency_ms=latency_ms,
        error=error,
    )
    session.add(msg)
    session.flush()
    conversation.updated_at = datetime.now(timezone.utc)
    session.flush()
    return msg


async def stream_reply(
    *,
    conversation: Conversation,
    user_content: str,
    llm: LLMChat,
    session: Session,
    settings: Settings,
    options: dict[str, Any] | None = None,
) -> AsyncIterator[dict]:
    """Run one user→assistant turn through the LLMChat port.

    Persists the user message immediately. Streams tokens to the caller via
    SSE-shaped dicts. Persists the assistant message (full or partial) on
    completion.

    Yields:
        {"event": "token", "data": str}                  per delta
        {"event": "usage", "data": {prompt_tok,
                                    completion_tok,
                                    gen_tps}}            once on done
        {"event": "done",  "data": {message_id: int}}    once
        {"event": "error", "data": {code, message}}      on upstream failure

    `options` is passed through verbatim to `LLMChat.chat_stream(options=...)`.
    Callers thread per-request flags (e.g. Sprint 5's `think: true`) here.
    Models that don't recognise an option ignore it (Ollama's behaviour).
    """
    _ = settings  # currently unused; reserved for future per-call options
    model = conversation.model or "default"
    user_msg = _persist_user_message(session, conversation, user_content)
    _ = user_msg
    history = _build_message_history(
        conversation=conversation,
        db_messages=_ordered_messages(session, conversation.id)[:-1],  # skip the just-persisted user row
        user_content=user_content,
    )
    session.commit()

    accumulated_text: list[str] = []
    final_usage_in: int | None = None
    final_usage_out: int | None = None
    final_eval_ns: int | None = None
    final_total_ns: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_persisted: str | None = None

    t0 = time.monotonic()
    try:
        async for chunk in llm.chat_stream(
            model=model, messages=history, options=options or None
        ):
            # Stream the delta to the client; Ollama may send empty deltas on
            # the final chunk — only forward non-empty ones to keep SSE quiet.
            if chunk.delta:
                accumulated_text.append(chunk.delta)
                yield {"event": "token", "data": chunk.delta}
            if chunk.done:
                final_usage_in = chunk.usage_in
                final_usage_out = chunk.usage_out
                final_eval_ns = chunk.eval_duration_ns
                final_total_ns = chunk.total_duration_ns
                break
    except OllamaModelNotFound as exc:
        error_code = "model_not_found"
        error_message = str(exc)
        error_persisted = "model_not_found"
    except OllamaUnreachableError as exc:
        error_code = "ollama_unreachable"
        error_message = str(exc)
        error_persisted = "ollama_unreachable"
    except OllamaStreamAbortedError as exc:
        error_code = "stream_aborted"
        error_message = str(exc)
        error_persisted = "stream_aborted"
    except OllamaResponseError as exc:
        error_code = "ollama_response_error"
        error_message = f"HTTP {exc.status}: {exc.body[:200]}"
        error_persisted = "ollama_response_error"
    except asyncio.CancelledError:
        # Client disconnected mid-stream. Persist whatever we have and let
        # the caller's cancellation propagate.
        error_persisted = "stream_aborted"
        # Persist before re-raising so the partial save is durable.
        gen_tps = _compute_gen_tps(final_usage_out, final_eval_ns)
        latency_ms = _ms_since(t0)
        assistant_msg = _persist_assistant_message(
            session,
            conversation,
            content="".join(accumulated_text),
            model=model,
            usage_in=final_usage_in,
            usage_out=final_usage_out,
            gen_tps=gen_tps,
            latency_ms=latency_ms,
            error=error_persisted,
        )
        session.commit()
        _ = assistant_msg  # explicit no-op for the linter
        raise

    gen_tps = _compute_gen_tps(final_usage_out, final_eval_ns)
    latency_ms = _ms_since(t0) if final_total_ns is None else int(final_total_ns / 1_000_000)
    assistant_msg = _persist_assistant_message(
        session,
        conversation,
        content="".join(accumulated_text),
        model=model,
        usage_in=final_usage_in,
        usage_out=final_usage_out,
        gen_tps=gen_tps,
        latency_ms=latency_ms,
        error=error_persisted,
    )
    session.commit()

    if error_code is not None:
        yield {
            "event": "error",
            "data": json.dumps({"code": error_code, "message": error_message}),
        }
        return

    yield {
        "event": "usage",
        "data": json.dumps(
            {
                "prompt_tok": final_usage_in,
                "completion_tok": final_usage_out,
                "gen_tps": gen_tps,
            }
        ),
    }
    yield {
        "event": "done",
        "data": json.dumps({"message_id": assistant_msg.id}),
    }


def _compute_gen_tps(usage_out: int | None, eval_ns: int | None) -> float | None:
    if not usage_out or not eval_ns:
        return None
    return usage_out / (eval_ns / 1e9)


def _ms_since(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def list_conversations(
    session: Session, *, user_id: int, mode: str
) -> list[Conversation]:
    return list(
        session.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id, Conversation.mode == mode)
            .order_by(Conversation.updated_at.desc())
        )
        .scalars()
    )


def get_conversation_for_user(
    session: Session, *, conversation_id: int, user_id: int, mode: str
) -> Conversation | None:
    return (
        session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
                Conversation.mode == mode,
            )
        )
        .scalars()
        .first()
    )


def conversation_message_count(session: Session, conversation_id: int) -> int:
    from sqlalchemy import func as sa_func

    return int(
        session.execute(
            select(sa_func.count(Message.id)).where(
                Message.conversation_id == conversation_id
            )
        ).scalar_one()
    )
