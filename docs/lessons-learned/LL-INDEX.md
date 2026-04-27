<!-- Status: Live | Updated: 2026-04-27 -->
# Lessons Learned — Index (LLM Cockpit)

Cockpit-local lessons captured at sprint reviews per PROCESS §4a-D. Each LL is one insight, written precisely; format mirrors AgenticBlox `lessons-learned/`.

Lessons that apply equally to AgenticBlox are **also** filed in `020 Projects/AgenticBlox/lessons-learned/` (or referenced from there) so the agent platform inherits them.

| LL | Title | Sprint | Theme |
|----|-------|--------|-------|
| — | (no entries yet — Sprint 0 has not yet completed) | — | — |

## Themes (for quick scanning)

- **Process** — what worked / didn't in spec-first, sprint flow, status transitions.
- **Stack** — FastAPI / Next.js / SQLite / SSE specifics; what surprised us.
- **Boundary** — DG-004 hits: scheduler client, Ollama client, vLLM, `nvidia-smi`. Real port/adapter pain.
- **UX** — what humans actually do with the chat / dashboard pages.
- **Ops** — Neuroforge systemd units, deploys, restarts, recovery.

## Cross-project references (cockpit lessons relevant to AgenticBlox)

The cockpit is the most demanding non-agent client of the queue layer (cockpit GOALS §3). Anything we learn about the scheduler queue, Ollama keep-alive, or `num_ctx` behaviour from cockpit traffic is a candidate for a parallel entry in `020 Projects/AgenticBlox/lessons-learned/`.

Existing AgenticBlox LLs that are particularly relevant for the cockpit: LL-006 (Neuroforge 24 GB ceiling), LL-008 (LiteLLM proxy), LL-015 (Ollama default placement unsuitable), LL-016 (RM52 thermal asymmetry), LL-017 (32 k context cap unvalidated). Read those before designing anything that touches model loading or context size.
