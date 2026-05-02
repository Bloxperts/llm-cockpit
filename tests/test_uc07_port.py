"""UC-07 LLMChat port + OllamaLLMChat adapter + FakeLLMChat tests.

Maps to docs/specs/test/UC-07-scheduler-routing.md (v0.2):
    T-01..T-09  OllamaLLMChat behaviour, happy + failure paths.
    T-10..T-12  FakeLLMChat seam.
    T-13, T-14  /api/tags + /api/ps wire-shape contract pinning.
    T-15        Grep boundary: only adapters/ imports httpx.
    T-16        Bootstrap probe consumes LLMChat via injection (T-16 here +
                full T-01..T-08 of UC-08 in tests/test_init.py still pass).

The chat_stream NDJSON wire-shape pinning is deferred to the slice that
wires chat_stream into the chat router (UC-04). See `class
TestChatStreamWireShapePending` at the bottom of this file.

Adapter tests use `httpx.MockTransport` for full control of responses
(including streaming NDJSON and mid-stream truncation). Bootstrap-probe
tests use `FakeLLMChat` directly.
"""

from __future__ import annotations

import ast
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from cockpit.adapters.fake_chat import FakeLLMChat, model_info
from cockpit.adapters.ollama_chat import OllamaLLMChat
from cockpit.ports.llm_chat import (
    ChatChunk,
    LoadedModel,
    LLMChat,
    ModelInfo,
    OllamaModelNotFound,
    OllamaResponseError,
    OllamaStreamAbortedError,
    OllamaUnreachableError,
    PullProgress,
)
from cockpit.services.bootstrap import (
    BootstrapError,
    InitOptions,
    probe_ollama,
    run_init,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "cockpit"

# --- Golden payloads (UC-07 Wire-shape contract test) ----------------------
#
# Captured against Ollama 0.1.x. Mutating any pinned key (rename / drop) must
# fail the parse — that's the contract. If a future Ollama major bump changes
# any of these, this test fails first, before any user-visible regression.

GOLDEN_TAGS_PAYLOAD = {
    "models": [
        {
            "name": "gemma3:27b",
            "modified_at": "2026-04-01T00:00:00Z",
            "size": 18000000000,
            "digest": "sha256:gemma3-aaaa",
        },
        {
            "name": "qwen3-coder:30b",
            "modified_at": "2026-04-02T00:00:00Z",
            "size": 22000000000,
            "digest": "sha256:qwen3coder-bbbb",
        },
    ]
}

GOLDEN_PS_PAYLOAD = {
    "models": [
        {
            "name": "gemma3:27b",
            "size_vram": 17000000000,
            "expires_at": "2026-04-27T13:00:00Z",
        },
        {
            "name": "ad-hoc:tiny",
            "size_vram": 500000000,
            # no expires_at -> until=None
        },
    ]
}

PINNED_TAGS_KEYS = ("name", "size", "modified_at", "digest")
PINNED_PS_KEYS = ("name", "size_vram", "expires_at")


# --- Helpers ---------------------------------------------------------------


def _adapter_against(handler) -> OllamaLLMChat:
    """Build an OllamaLLMChat backed by httpx.MockTransport."""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="http://ollama.test", transport=transport)
    return OllamaLLMChat("http://ollama.test", client=client)


def _json_response(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _text_response(status: int, text: str) -> httpx.Response:
    return httpx.Response(status, text=text)


def _ndjson_response(status: int, lines: list[dict]) -> httpx.Response:
    body = "".join(json.dumps(d) + "\n" for d in lines).encode("utf-8")
    return httpx.Response(
        status,
        content=body,
        headers={"content-type": "application/x-ndjson"},
    )


# --- T-01 list_models happy path ------------------------------------------


@pytest.mark.asyncio
async def test_list_models_happy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/tags"
        return _json_response(200, GOLDEN_TAGS_PAYLOAD)

    adapter = _adapter_against(handler)
    try:
        models = await adapter.list_models()
    finally:
        await adapter.aclose()

    assert len(models) == 2
    assert models[0] == ModelInfo(
        name="gemma3:27b",
        size_bytes=18000000000,
        modified=datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc),
        digest="sha256:gemma3-aaaa",
    )
    assert models[1].name == "qwen3-coder:30b"


# --- T-02 loaded happy path -----------------------------------------------


@pytest.mark.asyncio
async def test_loaded_parses_until_or_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/ps"
        return _json_response(200, GOLDEN_PS_PAYLOAD)

    adapter = _adapter_against(handler)
    try:
        loaded = await adapter.loaded()
    finally:
        await adapter.aclose()

    assert len(loaded) == 2
    assert loaded[0] == LoadedModel(
        name="gemma3:27b",
        size_vram=17000000000,
        until=datetime(2026, 4, 27, 13, 0, 0, tzinfo=timezone.utc),
    )
    assert loaded[1] == LoadedModel(name="ad-hoc:tiny", size_vram=500000000, until=None)


# --- T-03 chat_stream happy path ------------------------------------------


@pytest.mark.asyncio
async def test_chat_stream_yields_deltas_and_final_chunk() -> None:
    ndjson = [
        {"model": "gemma3:27b", "created_at": "t1", "message": {"role": "assistant", "content": "Hello"}, "done": False},
        {"model": "gemma3:27b", "created_at": "t2", "message": {"role": "assistant", "content": " world"}, "done": False},
        {
            "model": "gemma3:27b",
            "created_at": "t3",
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "prompt_eval_count": 7,
            "eval_count": 11,
            "prompt_eval_duration": 12345,
            "eval_duration": 67890,
            "total_duration": 99999,
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert body["model"] == "gemma3:27b"
        assert body["stream"] is True
        assert body["keep_alive"] == "5m"
        assert "options" not in body
        return _ndjson_response(200, ndjson)

    adapter = _adapter_against(handler)
    try:
        chunks: list[ChatChunk] = []
        async for ch in adapter.chat_stream(
            model="gemma3:27b",
            messages=[{"role": "user", "content": "hi"}],
            options={"keep_alive": "5m"},
        ):
            chunks.append(ch)
    finally:
        await adapter.aclose()

    assert [c.delta for c in chunks] == ["Hello", " world", ""]
    assert chunks[-1].done is True
    assert chunks[-1].usage_in == 7
    assert chunks[-1].usage_out == 11
    assert chunks[-1].eval_duration_ns == 67890
    assert chunks[-1].prompt_eval_duration_ns == 12345
    assert chunks[-1].total_duration_ns == 99999


# --- T-04 pull_model happy path -------------------------------------------


@pytest.mark.asyncio
async def test_pull_model_yields_progress() -> None:
    progress = [
        {"status": "pulling manifest"},
        {"status": "pulling abcd", "digest": "sha256:abcd", "total": 1000, "completed": 250},
        {"status": "pulling abcd", "digest": "sha256:abcd", "total": 1000, "completed": 1000},
        {"status": "success"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pull"
        body = json.loads(request.content)
        assert body["name"] == "gemma3:27b"
        return _ndjson_response(200, progress)

    adapter = _adapter_against(handler)
    try:
        items: list[PullProgress] = []
        async for p in adapter.pull_model("gemma3:27b"):
            items.append(p)
    finally:
        await adapter.aclose()

    assert items[0].status == "pulling manifest"
    assert items[1].digest == "sha256:abcd"
    assert items[1].completed == 250
    assert items[-1].status == "success"


# --- T-05 delete_model happy path -----------------------------------------


@pytest.mark.asyncio
async def test_delete_model_issues_delete_with_body() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/api/delete"
        seen["body"] = json.loads(request.content)
        return httpx.Response(200)

    adapter = _adapter_against(handler)
    try:
        result = await adapter.delete_model("gemma3:27b")
    finally:
        await adapter.aclose()

    assert result is None
    assert seen["body"] == {"name": "gemma3:27b"}


@pytest.mark.asyncio
async def test_delete_model_404_raises_model_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="model not found")

    adapter = _adapter_against(handler)
    try:
        with pytest.raises(OllamaModelNotFound):
            await adapter.delete_model("does-not-exist")
    finally:
        await adapter.aclose()


# --- T-06 unreachable -----------------------------------------------------


@pytest.mark.asyncio
async def test_list_models_against_unreachable_raises_unreachable() -> None:
    """Real adapter, real httpx, real socket → 127.0.0.1:1 is reserved."""
    adapter = OllamaLLMChat(
        "http://127.0.0.1:1",
        connect_timeout=1.0,
        read_timeout=1.0,
    )
    try:
        with pytest.raises(OllamaUnreachableError):
            await adapter.list_models()
    finally:
        await adapter.aclose()


# --- T-07 5xx response ----------------------------------------------------


@pytest.mark.asyncio
async def test_list_models_5xx_raises_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _text_response(500, "boom")

    adapter = _adapter_against(handler)
    try:
        with pytest.raises(OllamaResponseError) as exc:
            await adapter.list_models()
    finally:
        await adapter.aclose()
    assert exc.value.status == 500
    assert "boom" in exc.value.body


@pytest.mark.asyncio
async def test_loaded_5xx_raises_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _text_response(503, "service unavailable")

    adapter = _adapter_against(handler)
    try:
        with pytest.raises(OllamaResponseError) as exc:
            await adapter.loaded()
    finally:
        await adapter.aclose()
    assert exc.value.status == 503


# --- T-08 chat_stream 404 -> OllamaModelNotFound --------------------------


@pytest.mark.asyncio
async def test_chat_stream_404_model_not_found_raises_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _text_response(404, '{"error":"model \'ghost\' not found"}')

    adapter = _adapter_against(handler)
    try:
        with pytest.raises(OllamaModelNotFound) as exc:
            async for _ in adapter.chat_stream(
                model="ghost", messages=[{"role": "user", "content": "x"}]
            ):
                pass
    finally:
        await adapter.aclose()
    assert exc.value.model == "ghost"


@pytest.mark.asyncio
async def test_chat_stream_404_without_model_not_found_phrase_raises_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _text_response(404, "endpoint moved")

    adapter = _adapter_against(handler)
    try:
        with pytest.raises(OllamaResponseError) as exc:
            async for _ in adapter.chat_stream(
                model="m", messages=[{"role": "user", "content": "x"}]
            ):
                pass
    finally:
        await adapter.aclose()
    assert exc.value.status == 404


@pytest.mark.asyncio
async def test_chat_stream_5xx_raises_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _text_response(503, "overloaded")

    adapter = _adapter_against(handler)
    try:
        with pytest.raises(OllamaResponseError) as exc:
            async for _ in adapter.chat_stream(
                model="m", messages=[{"role": "user", "content": "x"}]
            ):
                pass
    finally:
        await adapter.aclose()
    assert exc.value.status == 503


# --- Coverage: defensive paths on every method ----------------------------


@pytest.mark.asyncio
async def test_loaded_against_unreachable_raises_unreachable() -> None:
    adapter = OllamaLLMChat("http://127.0.0.1:1", connect_timeout=1.0, read_timeout=1.0)
    try:
        with pytest.raises(OllamaUnreachableError):
            await adapter.loaded()
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_pull_model_against_unreachable_raises_unreachable() -> None:
    adapter = OllamaLLMChat("http://127.0.0.1:1", connect_timeout=1.0, read_timeout=1.0)
    try:
        with pytest.raises(OllamaUnreachableError):
            async for _ in adapter.pull_model("ghost"):
                pass
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_pull_model_5xx_raises_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _text_response(500, "registry down")

    adapter = _adapter_against(handler)
    try:
        with pytest.raises(OllamaResponseError) as exc:
            async for _ in adapter.pull_model("ghost"):
                pass
    finally:
        await adapter.aclose()
    assert exc.value.status == 500


@pytest.mark.asyncio
async def test_pull_model_skips_blank_lines() -> None:
    """NDJSON streams sometimes include keep-alive blank lines; the adapter
    must skip them rather than raise on json.loads("")."""
    body = (
        json.dumps({"status": "pulling manifest"}).encode("utf-8")
        + b"\n\n"
        + json.dumps({"status": "success"}).encode("utf-8")
        + b"\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/x-ndjson"})

    adapter = _adapter_against(handler)
    try:
        items = [p async for p in adapter.pull_model("m")]
    finally:
        await adapter.aclose()
    assert [p.status for p in items] == ["pulling manifest", "success"]


@pytest.mark.asyncio
async def test_chat_stream_skips_blank_lines() -> None:
    body = (
        json.dumps(
            {"model": "m", "created_at": "t", "message": {"role": "assistant", "content": "x"}, "done": False}
        ).encode("utf-8")
        + b"\n\n"
        + json.dumps(
            {
                "model": "m",
                "created_at": "t",
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "prompt_eval_count": 1,
                "eval_count": 1,
                "prompt_eval_duration": 1,
                "eval_duration": 1,
                "total_duration": 1,
            }
        ).encode("utf-8")
        + b"\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/x-ndjson"})

    adapter = _adapter_against(handler)
    try:
        chunks = [
            c
            async for c in adapter.chat_stream(
                model="m", messages=[{"role": "user", "content": "x"}]
            )
        ]
    finally:
        await adapter.aclose()
    assert [c.delta for c in chunks] == ["x", ""]
    assert chunks[-1].done is True


@pytest.mark.asyncio
async def test_delete_model_against_unreachable_raises_unreachable() -> None:
    adapter = OllamaLLMChat("http://127.0.0.1:1", connect_timeout=1.0, read_timeout=1.0)
    try:
        with pytest.raises(OllamaUnreachableError):
            await adapter.delete_model("m")
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_delete_model_5xx_raises_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _text_response(500, "boom")

    adapter = _adapter_against(handler)
    try:
        with pytest.raises(OllamaResponseError) as exc:
            await adapter.delete_model("m")
    finally:
        await adapter.aclose()
    assert exc.value.status == 500


# --- T-09 stream aborted (no done=true) -----------------------------------


@pytest.mark.asyncio
async def test_chat_stream_without_done_raises_aborted() -> None:
    truncated = [
        {"model": "m", "created_at": "t1", "message": {"role": "assistant", "content": "Hi"}, "done": False},
        {"model": "m", "created_at": "t2", "message": {"role": "assistant", "content": "..."}, "done": False},
        # never any done=true
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return _ndjson_response(200, truncated)

    adapter = _adapter_against(handler)
    try:
        with pytest.raises(OllamaStreamAbortedError):
            async for _ in adapter.chat_stream(
                model="m", messages=[{"role": "user", "content": "x"}]
            ):
                pass
    finally:
        await adapter.aclose()


# --- T-10 / T-11 / T-12 FakeLLMChat behaviour -----------------------------


@pytest.mark.asyncio
async def test_fake_list_and_loaded_records_call() -> None:
    fake = FakeLLMChat(
        models=[model_info("gemma3:27b"), model_info("qwen3-coder:30b")],
        loaded=[LoadedModel(name="gemma3:27b", size_vram=1, until=None)],
    )

    out_models = await fake.list_models()
    assert [m.name for m in out_models] == ["gemma3:27b", "qwen3-coder:30b"]
    assert fake.last_call == {"method": "list_models"}

    out_loaded = await fake.loaded()
    assert out_loaded[0].name == "gemma3:27b"
    assert fake.last_call == {"method": "loaded"}


@pytest.mark.asyncio
async def test_fake_chat_stream_yields_tokens_then_final_chunk() -> None:
    fake = FakeLLMChat(
        models=[model_info("m")],
        tokens=["a", "b", "c"],
        final_chunk=ChatChunk(delta="", done=True, usage_in=3, usage_out=3),
    )
    chunks = []
    async for ch in fake.chat_stream(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        options={"main_gpu": 0, "keep_alive": "24h"},
    ):
        chunks.append(ch)
    assert [c.delta for c in chunks] == ["a", "b", "c", ""]
    assert chunks[-1].done is True
    # Recorder captured the call args — useful for Sprint 3 placement-board tests.
    assert fake.last_call == {
        "method": "chat_stream",
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "options": {"main_gpu": 0, "keep_alive": "24h"},
    }


@pytest.mark.asyncio
async def test_fake_chat_stream_unknown_model_raises_not_found() -> None:
    fake = FakeLLMChat(models=[model_info("known")], known_models={"known"})
    with pytest.raises(OllamaModelNotFound):
        async for _ in fake.chat_stream(
            model="ghost", messages=[{"role": "user", "content": "x"}]
        ):
            pass


@pytest.mark.asyncio
async def test_fake_pull_and_delete_record_args() -> None:
    fake = FakeLLMChat(
        pull_progress=[PullProgress(status="success")],
        known_models={"to-delete"},
    )
    items = [p async for p in fake.pull_model("new")]
    assert items == [PullProgress(status="success")]
    assert fake.last_call == {"method": "pull_model", "model": "new"}

    await fake.delete_model("to-delete")
    assert fake.deleted == ["to-delete"]
    assert fake.last_call == {"method": "delete_model", "model": "to-delete"}


@pytest.mark.asyncio
async def test_fake_delete_unknown_model_raises_not_found() -> None:
    fake = FakeLLMChat(known_models={"known"})
    with pytest.raises(OllamaModelNotFound):
        await fake.delete_model("ghost")


@pytest.mark.asyncio
async def test_fake_can_simulate_unreachable() -> None:
    fake = FakeLLMChat(raise_on_list_models=OllamaUnreachableError("simulated down"))
    with pytest.raises(OllamaUnreachableError):
        await fake.list_models()


def test_fake_satisfies_protocol() -> None:
    """`FakeLLMChat` is structurally a `LLMChat` — runtime_checkable Protocol."""
    fake = FakeLLMChat()
    assert isinstance(fake, LLMChat)


# --- T-13 / T-14 wire-shape contract pinning ------------------------------


@pytest.mark.asyncio
async def test_wire_shape_tags_golden_parses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(200, GOLDEN_TAGS_PAYLOAD)

    adapter = _adapter_against(handler)
    try:
        models = await adapter.list_models()
    finally:
        await adapter.aclose()
    assert {m.name for m in models} == {"gemma3:27b", "qwen3-coder:30b"}


@pytest.mark.parametrize("dropped_key", PINNED_TAGS_KEYS)
@pytest.mark.asyncio
async def test_wire_shape_tags_dropping_pinned_key_breaks_parse(dropped_key: str) -> None:
    """Mutate the golden payload by removing one pinned key per parameterised
    case; assert the parse either raises or produces a meaningfully different
    result. This is the proof we actually depend on the pinned key set.
    """
    payload = json.loads(json.dumps(GOLDEN_TAGS_PAYLOAD))
    for entry in payload["models"]:
        entry.pop(dropped_key, None)

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(200, payload)

    adapter = _adapter_against(handler)
    try:
        if dropped_key == "name":
            # Models without `name` are filtered out — the parse "succeeds" but
            # the result is empty, which is still observably-different.
            models = await adapter.list_models()
            assert models == []
            return
        if dropped_key == "size":
            models = await adapter.list_models()
            assert all(m.size_bytes == 0 for m in models)
            return
        if dropped_key == "modified_at":
            models = await adapter.list_models()
            assert all(m.modified == datetime.fromtimestamp(0) for m in models)
            return
        if dropped_key == "digest":
            models = await adapter.list_models()
            assert all(m.digest == "" for m in models)
            return
    finally:
        await adapter.aclose()


@pytest.mark.asyncio
async def test_wire_shape_ps_golden_parses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(200, GOLDEN_PS_PAYLOAD)

    adapter = _adapter_against(handler)
    try:
        loaded = await adapter.loaded()
    finally:
        await adapter.aclose()
    assert [m.name for m in loaded] == ["gemma3:27b", "ad-hoc:tiny"]
    assert loaded[0].until is not None
    assert loaded[1].until is None


@pytest.mark.parametrize("dropped_key", PINNED_PS_KEYS)
@pytest.mark.asyncio
async def test_wire_shape_ps_dropping_pinned_key_breaks_parse(dropped_key: str) -> None:
    payload = json.loads(json.dumps(GOLDEN_PS_PAYLOAD))
    for entry in payload["models"]:
        entry.pop(dropped_key, None)

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(200, payload)

    adapter = _adapter_against(handler)
    try:
        if dropped_key == "name":
            loaded = await adapter.loaded()
            assert loaded == []
            return
        if dropped_key == "size_vram":
            loaded = await adapter.loaded()
            assert all(m.size_vram == 0 for m in loaded)
            return
        if dropped_key == "expires_at":
            loaded = await adapter.loaded()
            assert all(m.until is None for m in loaded)
            return
    finally:
        await adapter.aclose()


# --- T-15 grep boundary: only adapters/ may import httpx ------------------


def test_no_httpx_imports_outside_adapters() -> None:
    offenders: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        if "/adapters/" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "httpx" or alias.name.startswith("httpx."):
                        offenders.append(f"{path}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module == "httpx" or (node.module and node.module.startswith("httpx.")):
                    offenders.append(f"{path}:{node.lineno} from {node.module}")
    assert offenders == [], (
        "AC-1 violation: httpx imported outside src/cockpit/adapters/:\n"
        + "\n".join(offenders)
    )


def test_no_hardcoded_ollama_url_outside_config_and_adapter() -> None:
    """The literal default URL `http://127.0.0.1:11434` may only appear in
    `cockpit/config.py` (the canonical default) and `cockpit/adapters/`
    (where adapters might encode it as a fallback). Anywhere else is a leak.
    """
    forbidden = "http://127.0.0.1:11434"
    allowed_substrings = ("/config.py", "/adapters/")
    offenders: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if forbidden in text and not any(s in str(path) for s in allowed_substrings):
            offenders.append(str(path))
    assert offenders == [], (
        "AC-1 violation: hard-coded Ollama URL outside config/adapters:\n"
        + "\n".join(offenders)
    )


# --- T-16 bootstrap probe consumes the port via injection -----------------


def test_probe_ollama_via_fake_chat_no_socket(tmp_path: Path) -> None:
    """`probe_ollama` accepts a `chat_factory` returning any `LLMChat`. With
    a `FakeLLMChat` injected, the call returns model names without opening
    a socket — that's the seam.
    """
    fake = FakeLLMChat(
        models=[model_info("gemma3:27b"), model_info("qwen3-coder:30b")]
    )

    names = probe_ollama("http://ignored", chat_factory=lambda url: fake)
    assert names == ["gemma3:27b", "qwen3-coder:30b"]
    # `probe_ollama` calls `list_models()` once and `aclose()` once. Use
    # `calls_of(...)` rather than `last_call` since aclose overwrites the
    # latter.
    assert len(fake.calls_of("list_models")) == 1


def test_probe_ollama_maps_unreachable_to_bootstrap_error() -> None:
    """A factory whose adapter raises `OllamaUnreachableError` from
    `list_models` causes `probe_ollama` to raise `BootstrapError` with the
    install-guide hint — same UX as Slice A.
    """
    fake = FakeLLMChat(raise_on_list_models=OllamaUnreachableError("simulated"))
    with pytest.raises(BootstrapError) as exc:
        probe_ollama("http://ignored", chat_factory=lambda url: fake)
    assert "Cannot reach Ollama" in str(exc.value)
    assert exc.value.exit_code == 1


def test_probe_ollama_maps_response_error_to_bootstrap_error() -> None:
    fake = FakeLLMChat(
        raise_on_list_models=OllamaResponseError(503, "service unavailable")
    )
    with pytest.raises(BootstrapError) as exc:
        probe_ollama("http://ignored", chat_factory=lambda url: fake)
    assert "503" in str(exc.value)


def test_run_init_threads_chat_factory_through(tmp_path: Path) -> None:
    """Drive `run_init` end-to-end with a FakeLLMChat. No HTTP server needed —
    proves the DI seam covers UC-08 Slice A's existing happy path.
    """
    data_dir = tmp_path / "cockpit-data"
    data_dir.mkdir()
    fake = FakeLLMChat(
        models=[model_info("gemma3:27b"), model_info("qwen3-coder:30b")]
    )

    result = run_init(
        InitOptions(
            data_dir=data_dir,
            ollama_url="http://ignored",
            admin_password="PWchange1",
            bind="127.0.0.1",
            non_interactive=True,
        ),
        chat_factory=lambda url: fake,
    )
    assert sorted(result.discovered_models) == ["gemma3:27b", "qwen3-coder:30b"]
    assert result.tagged == {"gemma3:27b": "chat", "qwen3-coder:30b": "code"}


# --- chat_stream NDJSON wire-shape pinning (resolved in UC-04) ------------
#
# Captured against Ollama 0.1.x. Mutating any pinned key (rename / drop)
# must fail the parse — that's the contract. UC-04's chat router and
# UC-05's code router both depend on these keys (the cockpit reads
# usage_in / usage_out / gen_tps / latency_ms off the final chunk).


# Mid-stream chunk: minimal keys the parser dereferences.
GOLDEN_CHAT_STREAM_NON_DONE = {
    "model": "gemma3:27b",
    "created_at": "2026-04-28T00:00:00Z",
    "message": {"role": "assistant", "content": "Hello"},
    "done": False,
}

# Final chunk: usage and duration fields land here.
GOLDEN_CHAT_STREAM_FINAL = {
    "model": "gemma3:27b",
    "created_at": "2026-04-28T00:00:01Z",
    "message": {"role": "assistant", "content": ""},
    "done": True,
    "prompt_eval_count": 7,
    "eval_count": 11,
    "prompt_eval_duration": 12345,
    "eval_duration": 67890,
    "total_duration": 99999,
}

# The keys the cockpit dereferences. Mutating any of these breaks behaviour.
PINNED_CHAT_STREAM_NON_DONE_KEYS = ("done", "message")  # message.content checked separately
PINNED_CHAT_STREAM_FINAL_USAGE_KEYS = (
    "done",
    "prompt_eval_count",
    "eval_count",
    "prompt_eval_duration",
    "eval_duration",
    "total_duration",
)


def _ndjson_chat_stream_response(non_done: dict, final: dict) -> httpx.Response:
    body = (json.dumps(non_done) + "\n" + json.dumps(final) + "\n").encode("utf-8")
    return httpx.Response(
        200,
        content=body,
        headers={"content-type": "application/x-ndjson"},
    )


@pytest.mark.asyncio
async def test_wire_shape_chat_stream_golden_parses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/chat"
        return _ndjson_chat_stream_response(
            GOLDEN_CHAT_STREAM_NON_DONE, GOLDEN_CHAT_STREAM_FINAL
        )

    adapter = _adapter_against(handler)
    try:
        chunks: list[ChatChunk] = []
        async for ch in adapter.chat_stream(
            model="gemma3:27b",
            messages=[{"role": "user", "content": "hi"}],
        ):
            chunks.append(ch)
    finally:
        await adapter.aclose()

    assert chunks[0].delta == "Hello"
    assert chunks[0].done is False
    final = chunks[-1]
    assert final.done is True
    assert final.usage_in == 7
    assert final.usage_out == 11
    assert final.eval_duration_ns == 67890
    assert final.prompt_eval_duration_ns == 12345
    assert final.total_duration_ns == 99999


@pytest.mark.parametrize("dropped_key", PINNED_CHAT_STREAM_FINAL_USAGE_KEYS)
@pytest.mark.asyncio
async def test_wire_shape_chat_stream_dropping_pinned_final_key_breaks_parse(
    dropped_key: str,
) -> None:
    """If a future Ollama renames any of the final-chunk usage keys, the
    parser silently produces wrong values — that would be a quiet bug. This
    test makes the breakage loud and specific.
    """
    final = json.loads(json.dumps(GOLDEN_CHAT_STREAM_FINAL))
    final.pop(dropped_key, None)

    def handler(request: httpx.Request) -> httpx.Response:
        return _ndjson_chat_stream_response(GOLDEN_CHAT_STREAM_NON_DONE, final)

    adapter = _adapter_against(handler)
    try:
        chunks: list[ChatChunk] = []
        # If `done` is dropped, the adapter raises OllamaStreamAbortedError because
        # `saw_done` never flips True.
        if dropped_key == "done":
            with pytest.raises(OllamaStreamAbortedError):
                async for ch in adapter.chat_stream(
                    model="m",
                    messages=[{"role": "user", "content": "x"}],
                ):
                    chunks.append(ch)
            return
        async for ch in adapter.chat_stream(
            model="m",
            messages=[{"role": "user", "content": "x"}],
        ):
            chunks.append(ch)
    finally:
        await adapter.aclose()

    last = chunks[-1]
    # The dropped key shows up as `None` on the parsed chunk — observable
    # divergence from the golden, which would propagate into a `gen_tps`
    # of None and a missing usage row in `messages`.
    if dropped_key == "prompt_eval_count":
        assert last.usage_in is None
    elif dropped_key == "eval_count":
        assert last.usage_out is None
    elif dropped_key == "eval_duration":
        assert last.eval_duration_ns is None
    elif dropped_key == "prompt_eval_duration":
        assert last.prompt_eval_duration_ns is None
    elif dropped_key == "total_duration":
        assert last.total_duration_ns is None


@pytest.mark.asyncio
async def test_wire_shape_chat_stream_message_content_pinned() -> None:
    """The mid-stream `message.content` is the token text. If Ollama renames
    the path (e.g. `delta.content`) the cockpit's deltas go silent.
    """
    non_done = json.loads(json.dumps(GOLDEN_CHAT_STREAM_NON_DONE))
    non_done["message"].pop("content", None)
    non_done["message"]["delta"] = "Hello"  # plausible alternative shape

    def handler(request: httpx.Request) -> httpx.Response:
        return _ndjson_chat_stream_response(non_done, GOLDEN_CHAT_STREAM_FINAL)

    adapter = _adapter_against(handler)
    try:
        chunks: list[ChatChunk] = []
        async for ch in adapter.chat_stream(
            model="m",
            messages=[{"role": "user", "content": "x"}],
        ):
            chunks.append(ch)
    finally:
        await adapter.aclose()

    # No content on `message.content` → empty delta from the parser.
    # That's a meaningful regression — the cockpit would emit no token
    # events to the user even as Ollama happily streams.
    assert chunks[0].delta == ""


# --- Sanity: probe_ollama no longer makes a real HTTP call -----------------


def test_probe_ollama_does_not_open_socket_when_factory_returns_fake() -> None:
    """Belt-and-braces: even if the factory takes a junk URL like
    `http://example.invalid:99999`, no DNS lookup or connect should happen
    when the factory returns a fake.
    """
    fake = FakeLLMChat(models=[])
    # If this test ever takes longer than ~1 s, something has tried to hit
    # the network and the DI seam has regressed.
    import time as _t

    start = _t.monotonic()
    out = probe_ollama("http://example.invalid:99999", chat_factory=lambda url: fake)
    elapsed = _t.monotonic() - start
    assert out == []
    assert elapsed < 0.5, f"probe_ollama took {elapsed:.3f}s — DI seam regressed"


# --- Async-iterator ergonomics --------------------------------------------


def test_chat_stream_returns_async_generator() -> None:
    """Calling `chat_stream` should return an async-iterable immediately
    (no `await` on the call itself); `async for` is the only legal idiom.
    """
    fake = FakeLLMChat(models=[model_info("m")], tokens=["x"])
    gen = fake.chat_stream(model="m", messages=[{"role": "user", "content": "y"}])
    # An async generator object exposes __aiter__ / __anext__.
    assert hasattr(gen, "__aiter__")
    assert hasattr(gen, "__anext__")
    # Drain via asyncio.run for completeness.
    out = asyncio.run(_collect_async(gen))
    assert [c.delta for c in out] == ["x", ""]


async def _collect_async(gen) -> list:
    return [x async for x in gen]
