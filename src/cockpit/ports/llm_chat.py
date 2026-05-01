"""LLMChat — the cockpit's single outbound port for everything Ollama.

Per UC-07 functional spec §Port surface and ADR-003 §4. The port owns the
contract; the adapter owns the wire format. No code outside `adapters/`
opens a socket or imports `httpx`.

Exception hierarchy lives here (not on the adapter) so callers can `except`
on contract-level errors without importing the concrete adapter — DP-029
hexagonal compliance.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ModelInfo:
    name: str
    size_bytes: int
    modified: datetime
    digest: str


@dataclass(frozen=True)
class ModelDetails:
    name: str
    parameter_size: str | None = None
    quantization_level: str | None = None
    architecture_context_length: int | None = None
    capabilities: list[str] | None = None
    modified_at: datetime | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class LoadedModel:
    name: str
    size_vram: int
    until: datetime | None  # None means "no keep-alive ttl"


@dataclass(frozen=True)
class ChatChunk:
    delta: str  # token text (may be empty)
    done: bool
    usage_in: int | None = None  # only on done=True
    usage_out: int | None = None
    eval_duration_ns: int | None = None
    prompt_eval_duration_ns: int | None = None
    total_duration_ns: int | None = None


@dataclass(frozen=True)
class PullProgress:
    status: str
    digest: str | None = None
    total: int | None = None
    completed: int | None = None


@runtime_checkable
class LLMChat(Protocol):
    """The single outbound port. v0.1's only adapter is `OllamaLLMChat`."""

    async def list_models(self) -> list[ModelInfo]: ...

    async def show_model(self, model: str) -> ModelDetails: ...

    async def loaded(self) -> list[LoadedModel]: ...

    async def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[ChatChunk]: ...

    async def pull_model(self, model: str) -> AsyncIterator[PullProgress]: ...

    async def delete_model(self, model: str) -> None: ...


# --- Exceptions ------------------------------------------------------------
#
# Per UC-07 §Failure handling. Routers / services `except` these without
# knowing which adapter raised them.


class LLMChatError(Exception):
    """Base class — never raised directly; concrete subtypes always do."""


class OllamaUnreachableError(LLMChatError):
    """Connection refused, DNS failure, or connect-timeout. Routers map → 503."""


class OllamaResponseError(LLMChatError):
    """Ollama returned a 4xx/5xx outside the structured-error cases below.

    Carries the HTTP status and the (truncated) response body so the cockpit
    can surface the upstream message to the user.
    """

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Ollama returned HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


class OllamaModelNotFound(LLMChatError):
    """Ollama returned 404 for a model the caller asked about. Router → 404
    `{"detail": "model not found, refresh the picker"}`.
    """

    def __init__(self, model: str) -> None:
        super().__init__(f"model not found: {model}")
        self.model = model


class OllamaStreamAbortedError(LLMChatError):
    """The chat stream ended before Ollama emitted `done: true`. Router persists
    whatever was received with `messages.error = 'stream_aborted'`.
    """
