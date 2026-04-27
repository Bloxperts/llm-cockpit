"""FakeLLMChat — the in-memory test seam for the `LLMChat` port.

Routers / services that depend on `LLMChat` inject this in their unit tests
instead of the real adapter, so no socket is opened. Per UC-07 §Test seam +
ADR-002 v1.1.

Records every call into `last_call` so dashboard placement tests
(Sprint 3) can assert option values like `main_gpu`, `keep_alive`, etc.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from cockpit.ports.llm_chat import (
    ChatChunk,
    LoadedModel,
    ModelInfo,
    OllamaModelNotFound,
    PullProgress,
)


class FakeLLMChat:
    """Configurable static stand-in for an `LLMChat` implementation.

    Tests construct one with whatever canned data they need; the production
    adapter is never reached.
    """

    def __init__(
        self,
        *,
        models: list[ModelInfo] | None = None,
        loaded: list[LoadedModel] | None = None,
        tokens: list[str] | None = None,
        final_chunk: ChatChunk | None = None,
        pull_progress: list[PullProgress] | None = None,
        known_models: set[str] | None = None,
        raise_on_list_models: Exception | None = None,
        raise_on_loaded: Exception | None = None,
    ) -> None:
        self._models = list(models or [])
        self._loaded = list(loaded or [])
        self._tokens = list(tokens or [])
        self._final = final_chunk or ChatChunk(
            delta="",
            done=True,
            usage_in=10,
            usage_out=20,
            eval_duration_ns=1_000_000,
            prompt_eval_duration_ns=500_000,
            total_duration_ns=1_500_000,
        )
        self._pull_progress = list(pull_progress or [])
        self._known_models = set(known_models or {m.name for m in self._models})
        self._raise_on_list_models = raise_on_list_models
        self._raise_on_loaded = raise_on_loaded

        self.last_call: dict[str, Any] | None = None
        self.deleted: list[str] = []

    def _record(self, method: str, **kwargs: Any) -> None:
        self.last_call = {"method": method, **kwargs}

    async def list_models(self) -> list[ModelInfo]:
        self._record("list_models")
        if self._raise_on_list_models is not None:
            raise self._raise_on_list_models
        return list(self._models)

    async def loaded(self) -> list[LoadedModel]:
        self._record("loaded")
        if self._raise_on_loaded is not None:
            raise self._raise_on_loaded
        return list(self._loaded)

    async def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[ChatChunk]:
        self._record("chat_stream", model=model, messages=messages, options=options)
        if self._known_models and model not in self._known_models:
            raise OllamaModelNotFound(model)
        for tok in self._tokens:
            yield ChatChunk(delta=tok, done=False)
        yield self._final

    async def pull_model(self, model: str) -> AsyncIterator[PullProgress]:
        self._record("pull_model", model=model)
        for p in self._pull_progress:
            yield p

    async def delete_model(self, model: str) -> None:
        self._record("delete_model", model=model)
        if self._known_models and model not in self._known_models:
            raise OllamaModelNotFound(model)
        self.deleted.append(model)


def model_info(name: str, *, size_bytes: int = 1_000, digest: str = "sha256:fake") -> ModelInfo:
    """Convenience factory used in tests."""
    return ModelInfo(
        name=name,
        size_bytes=size_bytes,
        modified=datetime(2026, 4, 27, 12, 0, 0),
        digest=digest,
    )
