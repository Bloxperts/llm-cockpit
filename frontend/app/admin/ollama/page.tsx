"use client";

// UC-10 — admin Ollama configuration page.
//
// Four collapsible panels backed by the routes added in Sprint 9:
//   1. Model tags (data: GET /api/dashboard/snapshot)
//   2. Defaults (GET/PUT /api/admin/ollama/settings)
//   3. Per-model metrics (GET /api/admin/ollama/metrics, with row-click
//      drill-down to /metrics/{model})
//   4. Audit log (GET /api/admin/audit, paginated + filterable, with CSV
//      export via direct browser navigation to /export)
//
// Native <details>/<summary> for the accordion — no new UI library.
// Route guard: admin only; non-admins land back on /dashboard.

import { useEffect, useMemo, useState } from "react";

import { AppHeader } from "@/components/AppHeader";
import { ApiError, api } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import {
  DashboardSnapshot,
  ModelCardPayload,
  fmtBytes,
} from "@/lib/dashboard-types";

// --- Types ---------------------------------------------------------------

type TagValue = "chat" | "code" | "both";

interface SettingsBody {
  code_default_system_prompt: string | null;
  tag_heuristics_yaml: string | null;
}

interface ModelMetricsRow {
  model: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  mean_latency_ms: number | null;
  mean_gen_tps: number | null;
  last_call_at: string | null;
}

interface ModelCallEntry {
  role: string;
  usage_in: number | null;
  usage_out: number | null;
  latency_ms: number | null;
  gen_tps: number | null;
  ts: string;
  error: string | null;
}

interface ModelMetricsDrilldown {
  calls: ModelCallEntry[];
  p95_latency_ms: number | null;
}

interface AuditRow {
  source: "login" | "admin";
  ts: string;
  actor: string | null;
  action: string;
  target: string | null;
  details: Record<string, unknown> | null;
  source_ip: string | null;
}

interface AuditPage {
  entries: AuditRow[];
  total: number;
  page: number;
  per_page: number;
}

// --- Top-level page ------------------------------------------------------

export default function AdminOllamaPage() {
  const { me, loading } = useAuthStore();

  // Route guard.
  useEffect(() => {
    if (loading) return;
    if (!me) {
      window.location.replace("/login/");
      return;
    }
    if (me.role !== "admin") {
      window.location.replace("/dashboard/");
      return;
    }
  }, [me, loading]);

  if (loading || !me || me.role !== "admin") {
    return (
      <>
        <AppHeader />
        <main className="flex-1 flex items-center justify-center text-neutral-500">
          Loading…
        </main>
      </>
    );
  }

  return (
    <>
      <AppHeader />
      <main className="cockpit-page flex-1 space-y-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-neutral-900 dark:text-neutral-100">
            Ollama configuration
          </h1>
          <p className="text-sm text-neutral-600 dark:text-neutral-400">
            Model tags, defaults, metrics, and audit trail.
          </p>
        </div>

        <Panel summary="Model tags" defaultOpen>
          <ModelTagsPanel />
        </Panel>
        <Panel summary="Defaults">
          <SettingsPanel />
        </Panel>
        <Panel summary="Per-model metrics (last 7 days)">
          <MetricsPanel />
        </Panel>
        <Panel summary="Audit log">
          <AuditPanel />
        </Panel>
      </main>
    </>
  );
}

function Panel({
  summary,
  defaultOpen = false,
  children,
}: {
  summary: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  return (
    <details
      open={defaultOpen}
      className="cockpit-panel group"
    >
      <summary className="px-4 py-3 cursor-pointer select-none font-semibold text-sm text-neutral-900 dark:text-neutral-100 flex items-center gap-2">
        <span className="inline-block w-3 transition group-open:rotate-90">▶</span>
        {summary}
      </summary>
      <div className="px-4 pb-4 pt-2 border-t border-[var(--cockpit-border)]">
        {children}
      </div>
    </details>
  );
}

// --- Panel 1: Model tags -------------------------------------------------

function ModelTagsPanel() {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setError(null);
    try {
      const s = await api<DashboardSnapshot>("/api/dashboard/snapshot");
      setSnapshot(s);
    } catch (e) {
      setError(e instanceof ApiError ? `Failed: ${e.status}` : "Failed");
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function setTag(model: string, tag: TagValue) {
    setBusy(model);
    try {
      await api(`/api/admin/ollama/models/${encodeURIComponent(model)}/tag`, {
        method: "PATCH",
        body: JSON.stringify({ tag }),
      });
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? `Save failed: ${e.status}` : "Save failed");
    } finally {
      setBusy(null);
    }
  }

  async function clearOverride(model: string) {
    setBusy(model);
    try {
      await api(`/api/admin/ollama/models/${encodeURIComponent(model)}/tag`, {
        method: "DELETE",
      });
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? `Clear failed: ${e.status}` : "Clear failed");
    } finally {
      setBusy(null);
    }
  }

  async function deleteModel(m: ModelCardPayload) {
    if (!window.confirm(`Delete ${m.name} from Ollama? On-disk weights go away.`)) {
      return;
    }
    setBusy(m.name);
    try {
      await api(`/api/admin/ollama/models/${encodeURIComponent(m.name)}`, {
        method: "DELETE",
      });
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? `Delete failed: ${e.status}` : "Delete failed");
    } finally {
      setBusy(null);
    }
  }

  if (!snapshot) {
    return (
      <div className="text-sm text-neutral-500 dark:text-neutral-400">
        Loading models…
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {error ? (
        <div className="text-sm text-rose-600 dark:text-rose-400">{error}</div>
      ) : null}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
            <tr>
              <th className="text-left py-1 pr-3">Model</th>
              <th className="text-right py-1 px-3">Size</th>
              <th className="text-left py-1 px-3">Tag</th>
              <th className="text-left py-1 px-3">Source</th>
              <th className="text-left py-1 px-3">Actions</th>
            </tr>
          </thead>
          <tbody>
            {snapshot.models.map((m) => {
              const tag = (m.tag ?? "chat") as TagValue;
              const source = m.tag_source ?? "auto";
              return (
                <tr
                  key={m.name}
                  className="border-t border-neutral-200 dark:border-neutral-800"
                >
                  <td className="py-1.5 pr-3 font-mono">{m.name}</td>
                  <td className="py-1.5 px-3 text-right text-neutral-600 dark:text-neutral-400">
                    {fmtBytes(m.size_bytes)}
                  </td>
                  <td className="py-1.5 px-3">
                    <select
                      value={tag}
                      disabled={busy === m.name}
                      onChange={(e) => void setTag(m.name, e.target.value as TagValue)}
                      className="px-2 py-1 rounded border border-neutral-200 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm"
                    >
                      <option value="chat">chat</option>
                      <option value="code">code</option>
                      <option value="both">both</option>
                    </select>
                  </td>
                  <td className="py-1.5 px-3">
                    <span
                      className={`px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wide ${
                        source === "override"
                          ? "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200"
                          : "bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300"
                      }`}
                    >
                      {source}
                    </span>
                  </td>
                  <td className="py-1.5 px-3 space-x-2">
                    {source === "override" ? (
                      <button
                        onClick={() => void clearOverride(m.name)}
                        disabled={busy === m.name}
                        className="text-xs underline text-neutral-700 dark:text-neutral-300 hover:text-neutral-900 dark:hover:text-neutral-100"
                      >
                        Clear override
                      </button>
                    ) : null}
                    <button
                      onClick={() => void deleteModel(m)}
                      disabled={busy === m.name}
                      className="text-xs text-rose-600 dark:text-rose-400 hover:underline"
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <PullModelInline onDone={() => void refresh()} />
    </div>
  );
}

function PullModelInline({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [pulling, setPulling] = useState(false);
  const [log, setLog] = useState<string[]>([]);

  async function start() {
    if (!name.trim()) return;
    setPulling(true);
    setLog([]);
    try {
      const ctrl = new AbortController();
      const res = await fetch(
        `/api/admin/ollama/models/${encodeURIComponent(name.trim())}/pull`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: { Accept: "text/event-stream" },
          signal: ctrl.signal,
        },
      );
      if (!res.ok || !res.body) {
        setLog((l) => [...l, `error: HTTP ${res.status}`]);
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (line.startsWith("data:")) {
            setLog((l) => [...l, line.slice(5).trim()]);
          }
        }
      }
      onDone();
    } finally {
      setPulling(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 pt-3 mt-3 border-t border-neutral-200 dark:border-neutral-800">
      <div className="flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Pull a model — e.g. llama3:8b"
          className="flex-1 px-2 py-1 rounded border border-neutral-200 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm"
        />
        <button
          onClick={() => void start()}
          disabled={pulling || !name.trim()}
          className="px-3 py-1 rounded text-sm bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900 disabled:opacity-50"
        >
          {pulling ? "Pulling…" : "Pull"}
        </button>
      </div>
      {log.length > 0 ? (
        <pre className="max-h-40 overflow-y-auto text-xs bg-neutral-100 dark:bg-neutral-800 rounded p-2 font-mono">
          {log.join("\n")}
        </pre>
      ) : null}
    </div>
  );
}

// --- Panel 2: Defaults ---------------------------------------------------

function SettingsPanel() {
  const [body, setBody] = useState<SettingsBody | null>(null);
  const [draft, setDraft] = useState<SettingsBody>({
    code_default_system_prompt: "",
    tag_heuristics_yaml: "",
  });
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  useEffect(() => {
    api<SettingsBody>("/api/admin/ollama/settings")
      .then((s) => {
        setBody(s);
        setDraft({
          code_default_system_prompt: s.code_default_system_prompt ?? "",
          tag_heuristics_yaml: s.tag_heuristics_yaml ?? "",
        });
      })
      .catch(() => setFlash({ kind: "err", text: "Failed to load settings" }));
  }, []);

  async function save() {
    setSaving(true);
    setFlash(null);
    try {
      const payload: Record<string, string> = {};
      if (draft.code_default_system_prompt !== (body?.code_default_system_prompt ?? "")) {
        payload.code_default_system_prompt = draft.code_default_system_prompt ?? "";
      }
      if (draft.tag_heuristics_yaml !== (body?.tag_heuristics_yaml ?? "")) {
        payload.tag_heuristics_yaml = draft.tag_heuristics_yaml ?? "";
      }
      if (Object.keys(payload).length === 0) {
        setFlash({ kind: "ok", text: "No changes." });
        return;
      }
      const res = await api<{ updated: string[] }>(
        "/api/admin/ollama/settings",
        { method: "PUT", body: JSON.stringify(payload) },
      );
      setBody({
        code_default_system_prompt: draft.code_default_system_prompt,
        tag_heuristics_yaml: draft.tag_heuristics_yaml,
      });
      setFlash({
        kind: "ok",
        text: `Saved: ${res.updated.join(", ") || "(no changes)"}`,
      });
    } catch (e) {
      if (e instanceof ApiError && e.status === 400) {
        const detail = (e.detail as { detail?: { detail?: string; message?: string } })
          ?.detail;
        const msg = detail?.message ?? detail?.detail ?? "Bad request";
        setFlash({ kind: "err", text: `Save failed: ${msg}` });
      } else {
        setFlash({
          kind: "err",
          text: e instanceof ApiError ? `Save failed (${e.status})` : "Save failed",
        });
      }
    } finally {
      setSaving(false);
    }
  }

  if (body === null) {
    return <div className="text-sm text-neutral-500">Loading…</div>;
  }
  return (
    <div className="space-y-3">
      <label className="block">
        <div className="text-xs font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400 mb-1">
          Code mode default system prompt
        </div>
        <textarea
          value={draft.code_default_system_prompt ?? ""}
          onChange={(e) =>
            setDraft({ ...draft, code_default_system_prompt: e.target.value })
          }
          rows={8}
          className="w-full px-2 py-1 rounded border border-neutral-200 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm font-sans"
        />
      </label>
      <label className="block">
        <div className="text-xs font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400 mb-1">
          Tag heuristics (YAML)
        </div>
        <textarea
          value={draft.tag_heuristics_yaml ?? ""}
          onChange={(e) => setDraft({ ...draft, tag_heuristics_yaml: e.target.value })}
          rows={12}
          className="w-full px-2 py-1 rounded border border-neutral-200 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-xs font-mono"
          placeholder={"code_patterns:\n  - 'coder'\n  - 'codellama'\n"}
        />
      </label>
      <div className="flex items-center gap-3">
        <button
          onClick={() => void save()}
          disabled={saving}
          className="px-3 py-1.5 rounded text-sm bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900 disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save"}
        </button>
        {flash ? (
          <span
            className={`text-sm ${
              flash.kind === "ok"
                ? "text-emerald-600 dark:text-emerald-400"
                : "text-rose-600 dark:text-rose-400"
            }`}
          >
            {flash.text}
          </span>
        ) : null}
      </div>
    </div>
  );
}

// --- Panel 3: Per-model metrics -----------------------------------------

function MetricsPanel() {
  const [rows, setRows] = useState<ModelMetricsRow[] | null>(null);
  const [drill, setDrill] = useState<{ model: string; data: ModelMetricsDrilldown } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setError(null);
    try {
      const data = await api<ModelMetricsRow[]>("/api/admin/ollama/metrics");
      setRows(data);
    } catch (e) {
      setError(e instanceof ApiError ? `Failed: ${e.status}` : "Failed");
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function openDrill(model: string) {
    try {
      const data = await api<ModelMetricsDrilldown>(
        `/api/admin/ollama/metrics/${encodeURIComponent(model)}`,
      );
      setDrill({ model, data });
    } catch (e) {
      setError(e instanceof ApiError ? `Drill-down failed: ${e.status}` : "Drill-down failed");
    }
  }

  if (rows === null) {
    return <div className="text-sm text-neutral-500">Loading…</div>;
  }

  return (
    <div className="space-y-2">
      {error ? (
        <div className="text-sm text-rose-600 dark:text-rose-400">{error}</div>
      ) : null}
      <div className="flex justify-between items-center">
        <span className="text-xs text-neutral-500 dark:text-neutral-400">
          {rows.length} model{rows.length === 1 ? "" : "s"} with calls in the last 7 days
        </span>
        <button
          onClick={() => void refresh()}
          className="text-xs underline text-neutral-700 dark:text-neutral-300"
        >
          Refresh
        </button>
      </div>
      {rows.length === 0 ? (
        <div className="text-sm text-neutral-500 dark:text-neutral-400">
          No assistant calls in the last 7 days.
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
            <tr>
              <th className="text-left py-1 pr-3">Model</th>
              <th className="text-right py-1 px-3">Calls</th>
              <th className="text-right py-1 px-3">Tokens in</th>
              <th className="text-right py-1 px-3">Tokens out</th>
              <th className="text-right py-1 px-3">Avg ms</th>
              <th className="text-right py-1 px-3">Avg tps</th>
              <th className="text-left py-1 px-3">Last call</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.model}
                onClick={() => void openDrill(r.model)}
                className="border-t border-neutral-200 dark:border-neutral-800 cursor-pointer hover:bg-neutral-50 dark:hover:bg-neutral-800"
              >
                <td className="py-1.5 pr-3 font-mono">{r.model}</td>
                <td className="py-1.5 px-3 text-right">{r.calls}</td>
                <td className="py-1.5 px-3 text-right">{r.prompt_tokens}</td>
                <td className="py-1.5 px-3 text-right">{r.completion_tokens}</td>
                <td className="py-1.5 px-3 text-right">
                  {r.mean_latency_ms != null ? Math.round(r.mean_latency_ms) : "—"}
                </td>
                <td className="py-1.5 px-3 text-right">
                  {r.mean_gen_tps != null ? r.mean_gen_tps.toFixed(1) : "—"}
                </td>
                <td className="py-1.5 px-3 text-neutral-600 dark:text-neutral-400">
                  {r.last_call_at ? new Date(r.last_call_at).toLocaleString() : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {drill ? (
        <DrillDialog
          model={drill.model}
          data={drill.data}
          onClose={() => setDrill(null)}
        />
      ) : null}
    </div>
  );
}

function DrillDialog({
  model,
  data,
  onClose,
}: {
  model: string;
  data: ModelMetricsDrilldown;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white dark:bg-neutral-900 rounded-xl border border-neutral-200 dark:border-neutral-800 max-w-3xl w-full max-h-[80vh] overflow-y-auto p-4 space-y-3"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-baseline justify-between">
          <h3 className="font-semibold text-neutral-900 dark:text-neutral-100">
            {model} — last 50 calls
          </h3>
          <button
            onClick={onClose}
            className="text-sm text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
          >
            ✕
          </button>
        </div>
        <div className="text-sm text-neutral-700 dark:text-neutral-300">
          p95 latency:{" "}
          <span className="font-mono">
            {data.p95_latency_ms != null
              ? `${Math.round(data.p95_latency_ms)} ms`
              : "—"}
          </span>
        </div>
        <table className="w-full text-xs">
          <thead className="text-[10px] uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
            <tr>
              <th className="text-left py-1 pr-3">When</th>
              <th className="text-right py-1 px-3">Tokens in</th>
              <th className="text-right py-1 px-3">Tokens out</th>
              <th className="text-right py-1 px-3">Latency ms</th>
              <th className="text-right py-1 px-3">tps</th>
              <th className="text-left py-1 px-3">Error</th>
            </tr>
          </thead>
          <tbody>
            {data.calls.map((c, i) => (
              <tr
                key={i}
                className="border-t border-neutral-200 dark:border-neutral-800"
              >
                <td className="py-1 pr-3 text-neutral-600 dark:text-neutral-400">
                  {new Date(c.ts).toLocaleString()}
                </td>
                <td className="py-1 px-3 text-right">{c.usage_in ?? "—"}</td>
                <td className="py-1 px-3 text-right">{c.usage_out ?? "—"}</td>
                <td className="py-1 px-3 text-right">{c.latency_ms ?? "—"}</td>
                <td className="py-1 px-3 text-right">
                  {c.gen_tps != null ? c.gen_tps.toFixed(1) : "—"}
                </td>
                <td className="py-1 px-3 text-rose-600 dark:text-rose-400">
                  {c.error ?? ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// --- Panel 4: Audit log --------------------------------------------------

function AuditPanel() {
  const [page, setPage] = useState(1);
  const [perPage] = useState(50);
  const [filterAction, setFilterAction] = useState("");
  const [filterUsername, setFilterUsername] = useState("");
  const [appliedFilters, setAppliedFilters] = useState<{
    action: string;
    username: string;
  }>({ action: "", username: "" });
  const [data, setData] = useState<AuditPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  const queryString = useMemo(() => {
    const params = new URLSearchParams();
    params.set("page", String(page));
    params.set("per_page", String(perPage));
    if (appliedFilters.action) params.set("action", appliedFilters.action);
    if (appliedFilters.username) params.set("username", appliedFilters.username);
    return params.toString();
  }, [page, perPage, appliedFilters]);

  useEffect(() => {
    setError(null);
    api<AuditPage>(`/api/admin/audit?${queryString}`)
      .then((d) => setData(d))
      .catch((e) => {
        setError(e instanceof ApiError ? `Failed: ${e.status}` : "Failed");
      });
  }, [queryString]);

  function applyFilters() {
    setAppliedFilters({ action: filterAction, username: filterUsername });
    setPage(1);
  }

  function exportCsv() {
    const params = new URLSearchParams();
    if (appliedFilters.action) params.set("action", appliedFilters.action);
    if (appliedFilters.username) params.set("username", appliedFilters.username);
    const qs = params.toString();
    window.location.href = `/api/admin/audit/export${qs ? `?${qs}` : ""}`;
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.per_page)) : 1;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <input
          value={filterAction}
          onChange={(e) => setFilterAction(e.target.value)}
          placeholder="Action filter"
          className="px-2 py-1 rounded border border-neutral-200 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm"
        />
        <input
          value={filterUsername}
          onChange={(e) => setFilterUsername(e.target.value)}
          placeholder="Username filter"
          className="px-2 py-1 rounded border border-neutral-200 dark:border-neutral-700 bg-white dark:bg-neutral-900 text-sm"
        />
        <button
          onClick={applyFilters}
          className="px-3 py-1 rounded text-sm bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900"
        >
          Apply
        </button>
        <button
          onClick={exportCsv}
          className="px-3 py-1 rounded text-sm border border-neutral-300 dark:border-neutral-700"
        >
          Export CSV
        </button>
      </div>

      {error ? (
        <div className="text-sm text-rose-600 dark:text-rose-400">{error}</div>
      ) : null}
      {data === null ? (
        <div className="text-sm text-neutral-500">Loading…</div>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-[10px] uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
                <tr>
                  <th className="text-left py-1 pr-3">Time</th>
                  <th className="text-left py-1 px-3">Source</th>
                  <th className="text-left py-1 px-3">Actor</th>
                  <th className="text-left py-1 px-3">Action</th>
                  <th className="text-left py-1 px-3">Target</th>
                  <th className="text-left py-1 px-3">IP</th>
                </tr>
              </thead>
              <tbody>
                {data.entries.map((e, i) => (
                  <tr
                    key={i}
                    className="border-t border-neutral-200 dark:border-neutral-800"
                  >
                    <td className="py-1 pr-3 text-neutral-600 dark:text-neutral-400">
                      {new Date(e.ts).toLocaleString()}
                    </td>
                    <td className="py-1 px-3">
                      <span
                        className={`px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide ${
                          e.source === "admin"
                            ? "bg-sky-100 text-sky-800 dark:bg-sky-900 dark:text-sky-200"
                            : "bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300"
                        }`}
                      >
                        {e.source}
                      </span>
                    </td>
                    <td className="py-1 px-3 font-mono">{e.actor ?? "—"}</td>
                    <td className="py-1 px-3 font-mono">{e.action}</td>
                    <td className="py-1 px-3 font-mono">{e.target ?? "—"}</td>
                    <td className="py-1 px-3 text-neutral-600 dark:text-neutral-400">
                      {e.source_ip ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between text-xs text-neutral-500 dark:text-neutral-400">
            <span>
              {data.total} total · page {data.page} of {totalPages}
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={data.page <= 1}
                className="px-2 py-0.5 rounded border border-neutral-300 dark:border-neutral-700 disabled:opacity-50"
              >
                Prev
              </button>
              <button
                onClick={() => setPage((p) => p + 1)}
                disabled={data.page >= totalPages}
                className="px-2 py-0.5 rounded border border-neutral-300 dark:border-neutral-700 disabled:opacity-50"
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
