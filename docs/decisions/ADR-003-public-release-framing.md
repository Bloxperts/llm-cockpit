<!-- Status: Accepted | Version: 1.0 | Created: 2026-04-27 -->
# ADR-003 · Public release framing

**Status:** Accepted
**Date:** 2026-04-27
**Supersedes:** parts of ADR-002 (stack choices) — see §4

## Context

The original v0.1 cockpit specs assumed a **Neuroforge-internal tool**: hard-coded model names (`gemma4:26b`, `qwen3-coder:30b`), a queue layer at `127.0.0.1:8001` between cockpit and Ollama, NVIDIA GPU telemetry as required, and admin controls (power-cap, vLLM start/stop, sudoers) tied to that one host.

Chris has decided the cockpit ships as a **public open-source project**: anyone with Ollama running can `pip install llm-cockpit` and get a multi-user dashboard + chat UI. AgenticBlox-on-Neuroforge becomes one user of the public release, not the only one.

Three concrete requirements emerged from this:

1. The cockpit **takes Ollama as given.** It auto-detects Ollama at install time and learns about its environment (which models are available, what `OLLAMA_HOST` is set to). It does not manage Ollama's lifecycle.
2. The cockpit **runs on the same machine as Ollama by default,** falling back gracefully when configured otherwise.
3. **Documentation and installation must be clean enough for a stranger to follow.** Five-minute "from `git clone` to login screen" path, no Bloxperts-internal lore required.

## Decision

The cockpit becomes a portable, public release. The following framing applies from v0.1:

### 1. Distribution

- Pip-installable Python package (`llm-cockpit`). The frontend is built and shipped inside the Python wheel as static assets served by the FastAPI backend. One process, one port (default `8080`).
- A `cockpit-admin` CLI ships with the package, exposing the same operations as the admin UI plus headless management (`init`, `serve`, `user-add`, `user-set-password`, `user-list`, `migrate`).
- Public users get one of three install paths, in order of recommendation: `pip install llm-cockpit` + `cockpit-admin init`, Docker Compose one-liner, or `systemd-user` unit on Linux. The first is the v0.1 primary path.

### 2. Co-location with Ollama

- Default `COCKPIT_OLLAMA_URL=http://127.0.0.1:11434`. If `OLLAMA_HOST` is set in the environment, the bootstrap respects it.
- The bootstrap step (`cockpit-admin init`) probes the Ollama URL: if it answers `/api/tags`, the cockpit lists the available models in its first config and exits zero. If it does not answer, `init` exits non-zero with a clear error pointing at the Ollama install guide.
- **The cockpit does not start Ollama.** That is the operator's responsibility. The cockpit's responsibility ends at "report that Ollama is unreachable" and "list models that Ollama itself reports."

### 3. Authentication seed

- First-run bootstrap creates exactly one account: username `admin`, password `ollama`, role `admin`, `must_change_password=true`.
- The user is forced to change the password on first login. No other request is honoured for that user until the change happens (server-side enforcement).
- Additional users are created by the admin via the admin UI (or the `cockpit-admin user-add` CLI). All admin-created accounts default to `must_change_password=true` until the affected user logs in once and changes the password.

### 4. LLM transport

- One adapter for `LLMChat`: `OllamaLLMChat`, talking to `OLLAMA_URL/api/generate` and `/api/chat` with `stream: true`. SSE on the cockpit side; `text/event-stream` re-emission of Ollama's chunks.
- The original AgenticBlox **scheduler dependency is dropped from v0.1.** The cockpit knows nothing about port `8001`. If queue semantics matter for a deployment, that is solved *outside* the cockpit (e.g. by AgenticBlox proxying Ollama, or by a future v0.2 pluggable-adapter story).
- This **supersedes** the part of ADR-002 §Stack that referred to a scheduler client.

### 5. Telemetry is optional

- If `nvidia-smi` is on `PATH`, the cockpit samples GPU temp/VRAM/power every 5 s and renders the GPU panel. If not, the panel renders an empty state ("No GPU telemetry detected") and the rest of the dashboard works.
- This makes Mac-dev, Apple Silicon, AMD, and CPU-only setups first-class for v0.1.

### 6. Admin scope shrinks

- v0.1 admin = **user management only**. Add user, delete user, set role (`user` / `admin`), reset password.
- Pin / unpin / `num_ctx` / `keep_alive` / power-cap / vLLM controls move to v0.2 (working name: "Model Lifecycle" admin, US-V2). The original SPEC-006 content is preserved as an appendix on the new US-06 functional spec for reference; it is not implemented in v0.1.

### 7. Hard-coded model names removed

- Chat and Code pages list whatever Ollama returns from `/api/tags`. The user picks. The default selection is sticky per user (last-used model is remembered).
- "Code page" becomes a UI affordance (`?mode=code` toggle): the same chat backend, with code-emphasis rendering and a saved preference for whichever model the user last picked while in code mode. There is no separate `/api/code` route.

### 8. Documentation discipline

- The vault `README.md`, `GOALS.md`, and `architecture/COMPONENTS.md` are rewritten for an outsider audience: no implicit references to AgenticBlox, no Neuroforge-only addresses or model names in the body text. Bloxperts-internal context (Neuroforge IP, AgenticBlox sister project) lives in a clearly-labelled "Bloxperts deployment notes" appendix.
- The repo `README.md` mirrors that discipline: anyone can clone and run.

## Consequences

**Positive**

- The cockpit becomes useful to any Ollama user, not just AgenticBlox-on-Neuroforge.
- The architecture simplifies — one outbound port (`LLMChat` → Ollama), one optional outbound port (`Telemetry` → `nvidia-smi`), one inbound port (HTTP). DG-004 work shrinks accordingly.
- Sprint 1 has a clear architecture-design objective; Sprint 2 has clear build content.
- AgenticBlox is unaffected: it can wrap or proxy the cockpit if it ever wants queue semantics for its users.

**Negative**

- US-06 (admin controls) is rewritten. The existing draft content for pin/unpin/num_ctx is preserved as an appendix but loses its v0.1 status.
- US-07 is rewritten from "scheduler routing" to "Ollama integration". The DG-004 block is simpler but the spec is essentially new.
- The pip-distributable shape requires the Next.js frontend to be built and bundled into the Python package. This is well-trodden but new for the cockpit; it is the architecture sprint's job to figure out the build.

**Neutral**

- The `develop` / `main` two-branch flow, sprint cadence, and Spec-First discipline are unchanged.
- The DPs adopted in `DP-INDEX.md` v1.0 still apply; only the *interpretation column* shifts in a few places (DP-013 still applies; DP-014 now governs Ollama-side `keep_alive` rather than scheduler heavy slots).

## Compliance

- DP-007 (simplicity) — explicitly applied. One transport, one telemetry source, one admin scope.
- DP-008 (escape-hatch) — `LLMChat` port is preserved; future adapters (vLLM direct, scheduler proxy) plug in without rewriting the chat router.
- DP-012 (local-first) — strengthened: cockpit is now local-first by default, no upstream dependencies for v0.1.
- DP-027 (right delivery form) — the form is still "web service" (recorded in ADR-002 §DG-003 block); pip + CLI is the *delivery vehicle* for that form.
- DP-031 (progressive autonomy) — first-login password change is a textbook progressive-autonomy moment (the user can read but cannot act until the change happens).

## Follow-up

- Sprint 1 (architecture) walks all 9 functional specs to `Accepted` and produces a rewritten `architecture/COMPONENTS.md` reflecting this framing.
- Sprint 2 (first build) implements US-08 (installer + bootstrap) → US-09 (first-login password change) → US-01 (login). After that we can chain US-02 (dashboard) and US-04 (chat).
