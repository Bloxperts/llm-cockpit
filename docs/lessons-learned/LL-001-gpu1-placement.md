<!-- Status: Accepted | Created: 2026-04-29 | Accepted: 2026-04-29 | Sprint: 10 -->
# LL-001 · GPU1 placement needs host-level proof, not UI inference

## Context

Sprint 10 contains a diagnose-only slice for BUG-GPU1: dragging or selecting a model into the GPU 1 placement column does not reliably result in the model actually landing on GPU 1.

The cockpit currently runs one Ollama daemon and sends per-call placement hints through `LLMChat.chat_stream(..., options={ keep_alive, main_gpu, num_gpu })`. ADR-005 already records the important constraint: `main_gpu` is a hint, not hard pinning. Hard isolation would require separate Ollama processes with GPU visibility constrained at the process level.

## Current evidence

Code audit on 2026-04-29 shows:

- `placement="gpu1"` resolves to `options={"keep_alive": 86400, "main_gpu": 1}` through `_options_for_placement()`.
- The placement endpoint samples telemetry before and after warm-up and can return `mismatch=true` plus `main_gpu_actual` when VRAM growth is strongest on a different GPU.
- The dashboard snapshot serializer currently sets `actual.main_gpu_actual = null` and `actual.mismatch = false` every time it builds a model card. Therefore, even if `POST /place` detects a mismatch in its immediate response, the live card can later hide that evidence unless the frontend captures or persists it.
- Local development machine has no `nvidia-smi`, so no real GPU placement observation can be made there.

Neuroforge reproduction on 2026-04-29 shows:

- Host: `neuroforge`, two NVIDIA GeForce RTX 3090 cards.
- Driver state after reboot was healthy: `nvidia-smi` and `/proc/driver/nvidia/version` both reported `580.142`.
- GPU UUIDs:
  - GPU 0: `GPU-dda211b1-573e-ba22-dc2a-d63a62f9a577`
  - GPU 1: `GPU-1181ebc6-8b1a-99cb-f230-b89d06286c0c`
- Baseline before the load:
  - `/api/ps`: `{"models":[]}`
  - GPU 0: `1 MiB`
  - GPU 1: `1 MiB`
- Direct Ollama request using the cockpit's effective placement hint shape:

```json
{
  "model": "phi4:14b",
  "messages": [{ "role": "user", "content": "Say ok." }],
  "stream": false,
  "options": {
    "keep_alive": 86400,
    "main_gpu": 1
  }
}
```

- Result:
  - The model loaded successfully.
  - `/api/ps` showed `phi4:14b` loaded with `size_vram=11556747946`.
  - `nvidia-smi` showed GPU 0 at `10752 MiB` and GPU 1 at `4 MiB`.
  - Ollama service logs showed `CUDA0 model buffer size = 8354.71 MiB` and `CUDA0 KV buffer size = 1700.03 MiB`.

Conclusion from this reproduction: even when the request carries `main_gpu=1`, Ollama placed the tested model on GPU 0. This supports BUG-GPU1 as an Ollama scheduling / process-visibility limitation rather than a placement-board drag-and-drop failure.

Upstream evidence:

- Official Ollama GPU documentation says multi-GPU selection is controlled by server environment variables such as `CUDA_VISIBLE_DEVICES`; UUIDs are preferred over numeric IDs because ordering can vary.
- Ollama issue `ollama/ollama#6493` reports `main_gpu` not reliably being respected on multi-GPU setups.

## Reproduction protocol for Neuroforge

Run this on the dual-GPU host, not on a Mac or CPU-only dev box.

1. Start from a clean dashboard state on the Sprint 10 branch.
2. Log in as an admin.
3. Pick a model that fits on either single GPU.
4. Record baseline:
   - `nvidia-smi -L`
   - `nvidia-smi --query-gpu=index,uuid,memory.used,memory.total --format=csv,noheader,nounits`
   - `curl http://127.0.0.1:11434/api/ps`
5. Move the model to GPU 1 from the placement board.
6. Capture the exact `POST /api/admin/ollama/models/{model}/place` response, especially:
   - `applied.main_gpu`
   - `mismatch`
   - `main_gpu_actual`
7. Cold-load or run the Sprint 10 perf-test drawer.
8. During and after load, capture:
   - `nvidia-smi --query-gpu=index,uuid,memory.used,memory.total --format=csv,noheader,nounits`
   - `curl http://127.0.0.1:11434/api/ps`
   - perf-test `gpu_layout_diff`
9. Repeat once after unloading the model to avoid warm-state contamination.

## What to decide after reproduction

Choose exactly one recommendation:

- **Document and accept best-effort placement** if `main_gpu=1` is sent correctly and Ollama still chooses GPU 0 or spreads unpredictably.
- **Env-var fix** if constraining the Ollama server process with `CUDA_VISIBLE_DEVICES` or GPU UUIDs is sufficient for the desired operational behavior.
- **Separate Ollama process per GPU** if admins need hard per-GPU control inside the cockpit. This is an architecture change and must go through ADR-level review.
- **Code-level fix** only if the cockpit is failing to send the correct hint, failing to capture the placement response, or hiding known mismatch data.

## Current recommendation

Recommendation for Sprint 10:

**Ollama placement is best-effort; document and accept for v0.4.0.** Do not commit a GPU1 placement fix in this sprint.

The cockpit is sending the expected `main_gpu=1` hint, but the single Ollama daemon still loaded `phi4:14b` onto CUDA0 on Neuroforge. If hard GPU isolation becomes a product requirement, the likely architecture is one Ollama process per GPU with process-level visibility constrained via UUID-based `CUDA_VISIBLE_DEVICES` / `OLLAMA_GPU_DEVICES`, not a frontend placement-board fix.

One small Cockpit follow-up is worth considering in a later patch: preserve or surface the immediate `POST /place` mismatch evidence in the dashboard instead of resetting `actual.main_gpu_actual` and `actual.mismatch` on the next snapshot.
