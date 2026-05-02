"""OllamaLLMChat — the only `LLMChat` adapter in v0.1.

Talks directly to one Ollama daemon over HTTP. This is the **only** module
in the cockpit that imports `httpx` or knows Ollama's wire format. Everything
else depends on the `LLMChat` port.

References:
- UC-07 functional spec §Adapter (URL routes, timeouts).
- UC-07 functional spec §Failure handling (exception mapping).
- ADR-002 v1.1 (httpx + async).
- ADR-003 §4 (no scheduler; cockpit talks to Ollama directly).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx

from cockpit.ports.llm_chat import (
    ChatChunk,
    LoadedModel,
    ModelDetails,
    ModelInfo,
    OllamaModelNotFound,
    OllamaResponseError,
    OllamaStreamAbortedError,
    OllamaUnreachableError,
    PullProgress,
)

# UC-07 §Adapter — 5 s connect, 900 s read (long generations).
DEFAULT_CONNECT_TIMEOUT_S = 5.0
DEFAULT_READ_TIMEOUT_S = 900.0


def _parse_iso_datetime(s: str | None) -> datetime | None:
    """Parse Ollama's ISO-8601 timestamps. Tolerates the trailing `Z`."""
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class OllamaLLMChat:
    """`LLMChat` adapter against a single Ollama daemon.

    The constructor accepts an optional pre-built `httpx.AsyncClient` so unit
    tests can inject a `MockTransport`. In production the adapter builds its
    own client with the spec-mandated timeouts.
    """

    def __init__(
        self,
        url: str,
        *,
        client: httpx.AsyncClient | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_S,
        read_timeout: float = DEFAULT_READ_TIMEOUT_S,
    ) -> None:
        self.url = url.rstrip("/")
        self._owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(
                base_url=self.url,
                timeout=httpx.Timeout(connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout),
            )
        self._client = client

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # --- list_models -------------------------------------------------------

    async def list_models(self) -> list[ModelInfo]:
        try:
            resp = await self._client.get("/api/tags")
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise OllamaUnreachableError(f"{self.url}: {exc!s}") from exc
        if resp.status_code != 200:
            raise OllamaResponseError(resp.status_code, resp.text)
        payload = resp.json()
        return [
            ModelInfo(
                name=m["name"],
                size_bytes=int(m.get("size", 0)),
                modified=_parse_iso_datetime(m.get("modified_at"))  # type: ignore[arg-type]
                or datetime.fromtimestamp(0),
                digest=m.get("digest", ""),
            )
            for m in (payload.get("models") or [])
            if "name" in m
        ]

    # --- show_model --------------------------------------------------------

    async def show_model(self, model: str) -> ModelDetails:
        try:
            resp = await self._client.post("/api/show", json={"name": model})
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise OllamaUnreachableError(f"{self.url}: {exc!s}") from exc
        if resp.status_code == 404:
            raise OllamaModelNotFound(model)
        if resp.status_code != 200:
            raise OllamaResponseError(resp.status_code, resp.text)
        payload = resp.json()
        return _parse_model_details(model, payload)

    # --- loaded ------------------------------------------------------------

    async def loaded(self) -> list[LoadedModel]:
        try:
            resp = await self._client.get("/api/ps")
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise OllamaUnreachableError(f"{self.url}: {exc!s}") from exc
        if resp.status_code != 200:
            raise OllamaResponseError(resp.status_code, resp.text)
        payload = resp.json()
        return [
            LoadedModel(
                name=m["name"],
                size_vram=int(m.get("size_vram", 0)),
                until=_parse_iso_datetime(m.get("expires_at")),
            )
            for m in (payload.get("models") or [])
            if "name" in m
        ]

    # --- chat_stream -------------------------------------------------------

    async def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[ChatChunk]:
        """Stream NDJSON chunks from `POST /api/chat`. Re-emits each as a
        `ChatChunk`; the final chunk carries the usage and duration fields.

        Raises:
            OllamaUnreachableError — connect / read timeout / DNS failure.
            OllamaModelNotFound    — Ollama 404 with `model not found` body.
            OllamaResponseError    — any other 4xx/5xx.
            OllamaStreamAbortedError — stream closed before `done: true`.
        """
        body: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        request_options = dict(options or {})
        if "keep_alive" in request_options:
            body["keep_alive"] = request_options.pop("keep_alive")
        if request_options:
            body["options"] = request_options

        try:
            async with self._client.stream("POST", "/api/chat", json=body) as resp:
                if resp.status_code == 404:
                    text = await resp.aread()
                    if b"model" in text and b"not found" in text:
                        raise OllamaModelNotFound(model)
                    raise OllamaResponseError(404, text.decode("utf-8", errors="replace"))
                if resp.status_code != 200:
                    text = await resp.aread()
                    raise OllamaResponseError(resp.status_code, text.decode("utf-8", errors="replace"))
                saw_done = False
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    chunk = _parse_chat_chunk(line)
                    if chunk.done:
                        saw_done = True
                    yield chunk
                if not saw_done:
                    raise OllamaStreamAbortedError(
                        "Ollama chat stream ended before emitting done=true"
                    )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise OllamaUnreachableError(f"{self.url}: {exc!s}") from exc
        except httpx.ReadError as exc:
            raise OllamaStreamAbortedError(f"{self.url}: {exc!s}") from exc

    # --- pull_model --------------------------------------------------------

    async def pull_model(self, model: str) -> AsyncIterator[PullProgress]:
        try:
            async with self._client.stream(
                "POST", "/api/pull", json={"name": model, "stream": True}
            ) as resp:
                if resp.status_code != 200:
                    text = await resp.aread()
                    raise OllamaResponseError(
                        resp.status_code, text.decode("utf-8", errors="replace")
                    )
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    yield _parse_pull_progress(line)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise OllamaUnreachableError(f"{self.url}: {exc!s}") from exc

    # --- delete_model ------------------------------------------------------

    async def delete_model(self, model: str) -> None:
        try:
            resp = await self._client.request(
                "DELETE", "/api/delete", json={"name": model}
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise OllamaUnreachableError(f"{self.url}: {exc!s}") from exc
        if resp.status_code == 404:
            raise OllamaModelNotFound(model)
        if resp.status_code not in (200, 204):
            raise OllamaResponseError(resp.status_code, resp.text)
        return None


# --- private parsing helpers ----------------------------------------------


def _parse_chat_chunk(line: str) -> ChatChunk:
    """Parse one NDJSON line from `/api/chat` into a `ChatChunk`.

    Key set this slice depends on (UC-07 §Wire-shape contract test):
        model, created_at, message.role, message.content, done,
        prompt_eval_count, eval_count, prompt_eval_duration, eval_duration,
        total_duration

    Per-key sensitivity testing for these keys is deferred to the chat_stream
    slice (UC-04). We still read them here.
    """
    obj = json.loads(line)
    message = obj.get("message") or {}
    delta = message.get("content") or ""
    done = bool(obj.get("done"))
    if done:
        return ChatChunk(
            delta=delta,
            done=True,
            usage_in=obj.get("prompt_eval_count"),
            usage_out=obj.get("eval_count"),
            eval_duration_ns=obj.get("eval_duration"),
            prompt_eval_duration_ns=obj.get("prompt_eval_duration"),
            total_duration_ns=obj.get("total_duration"),
        )
    return ChatChunk(delta=delta, done=False)


def _parse_pull_progress(line: str) -> PullProgress:
    obj = json.loads(line)
    return PullProgress(
        status=obj.get("status", ""),
        digest=obj.get("digest"),
        total=obj.get("total"),
        completed=obj.get("completed"),
    )


def _first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _parse_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_model_details(model: str, payload: dict[str, Any]) -> ModelDetails:
    details = payload.get("details") or {}
    model_info = payload.get("model_info") or {}
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list):
        capabilities = None

    ctx = _parse_optional_int(
        _first_present(
            model_info,
            (
                "llama.context_length",
                "qwen2.context_length",
                "gemma3.context_length",
                "gemma2.context_length",
                "general.context_length",
            ),
        )
    )
    return ModelDetails(
        name=model,
        parameter_size=details.get("parameter_size") or details.get("parameters"),
        quantization_level=details.get("quantization_level") or details.get("quantization"),
        architecture_context_length=ctx,
        capabilities=[str(c) for c in capabilities] if capabilities else None,
        modified_at=_parse_iso_datetime(payload.get("modified_at")),
        raw=payload,
    )
