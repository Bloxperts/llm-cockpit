"use client";

import { useEffect, useMemo, useState } from "react";
import {
  DndContext,
  DragEndEvent,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
} from "@dnd-kit/core";

import { AppHeader } from "@/components/AppHeader";
import { DashboardHistory } from "@/components/DashboardHistory";
import { ApiError, api, streamSse } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import {
  COLUMN_LABELS,
  DashboardSnapshot,
  ModelCardPayload,
  fmtBytes,
  isWarmColumn,
} from "@/lib/dashboard-types";

type DashboardTab = "live" | "history";
type PerfStatus = "idle" | "running" | "result" | "cancelled" | "error";
type ModelFilter = "all" | "loaded" | "missing_perf" | "warnings";

type PerfResult = {
  cold_load_seconds?: number | null;
  throughput_tps?: number | null;
  max_ctx_observed?: number | null;
  gpu_layout_diff?: Record<string, number>;
};

type PerfRunState = {
  model: string;
  status: PerfStatus;
  stage: string;
  elapsedMs: number;
  tokensSoFar: number | null;
  tokensPerSec: number | null;
  lastEventAt: number;
  result: PerfResult | null;
  error: string | null;
  cancelling: boolean;
};

// Sprint 5b — RTX 3090 (Ampere GPU Boost 4.0) thresholds. These are the
// rated values for the codebase's reference card; they're a reasonable
// proxy for any modern NVIDIA part. If the cockpit ever needs per-SKU
// thresholds, this constant becomes a function of the GPU model.
const GPU_TEMP_THRESHOLDS = [
  { max: 70, label: "Good", cls: "bg-emerald-500 text-white" },
  { max: 82, label: "Workload", cls: "bg-sky-500 text-white" },
  { max: 89, label: "Throttling", cls: "bg-amber-500 text-white" },
  { max: Infinity, label: "Critical", cls: "bg-rose-600 text-white" },
] as const;

function gpuTempStatus(tempC: number | null) {
  if (tempC === null) return null;
  return GPU_TEMP_THRESHOLDS.find((t) => tempC <= t.max)!;
}

const DEFAULT_TDP_W = 350; // RTX 3090 factory TDP — fallback when nvidia-smi power.limit is null.

function wattsColor(currentW: number | null, maxW: number | null) {
  if (currentW === null || maxW === null || maxW <= 0) {
    return "text-neutral-700 dark:text-neutral-300";
  }
  const pct = (currentW / maxW) * 100;
  if (pct <= 70) return "text-emerald-600 dark:text-emerald-400";
  if (pct <= 90) return "text-amber-600 dark:text-amber-400";
  return "text-rose-600 dark:text-rose-400";
}

export default function DashboardPage() {
  const { me, loading } = useAuthStore();
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [busyModel, setBusyModel] = useState<string | null>(null);
  const [perfRun, setPerfRun] = useState<PerfRunState | null>(null);
  const [perfTick, setPerfTick] = useState(Date.now());
  const [placementError, setPlacementError] = useState<string | null>(null);
  const [filter, setFilter] = useState<ModelFilter>("all");
  const [metadataBusy, setMetadataBusy] = useState(false);
  const [testAllBusy, setTestAllBusy] = useState(false);
  const [loadModelOpen, setLoadModelOpen] = useState(false);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }));
  // UC-03 — top-level Live / History tab. The Live SSE stream still
  // runs in the background regardless of which tab is shown so
  // switching back to Live is instant.
  const [tab, setTab] = useState<DashboardTab>("live");

  // Initial load + SSE stream.
  useEffect(() => {
    if (loading) return;
    if (!me) {
      window.location.replace("/login/");
      return;
    }
    if (me.must_change_password) {
      window.location.replace("/change-password/");
      return;
    }

    let cancelled = false;

    void api<DashboardSnapshot>("/api/dashboard/snapshot")
      .then((s) => {
        if (!cancelled) setSnapshot(s);
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) {
          window.location.replace("/login/");
        }
      });

    const es = new EventSource("/api/dashboard/stream", {
      withCredentials: true,
    });
    es.addEventListener("snapshot", (ev: MessageEvent) => {
      try {
        const parsed = JSON.parse(ev.data) as DashboardSnapshot;
        if (!cancelled) setSnapshot(parsed);
      } catch {
        // ignore malformed chunks
      }
    });
    return () => {
      cancelled = true;
      es.close();
    };
  }, [me, loading]);

  useEffect(() => {
    const id = window.setInterval(() => setPerfTick(Date.now()), 250);
    return () => window.clearInterval(id);
  }, []);

  async function refreshSnapshot() {
    const fresh = await api<DashboardSnapshot>("/api/dashboard/snapshot");
    setSnapshot(fresh);
  }

  async function onPlacementChange(
    model: string,
    placement: string,
    extras: { keep_alive_mode?: string; keep_alive_seconds?: number; num_ctx_default?: number | null } = {},
  ) {
    const previous = snapshot;
    const current = snapshot?.models.find((m) => m.name === model)?.config?.placement ?? "on_demand";
    if (snapshot && placement === "multi_gpu" && current !== "multi_gpu") {
      const warning = crossGpuEvictionWarning(snapshot, model);
      if (warning && !window.confirm(warning)) {
        return;
      }
    }
    setBusyModel(model);
    setPlacementError(null);
    setSnapshot((prev) =>
      prev
        ? {
            ...prev,
            models: prev.models.map((m) =>
              m.name === model ? { ...m, config: { ...m.config, placement } } : m,
            ),
          }
        : prev,
    );
    try {
      await api(`/api/admin/ollama/models/${encodeURIComponent(model)}/place`, {
        method: "POST",
        body: JSON.stringify({ placement, ...extras }),
      });
    } catch (e) {
      setSnapshot(previous);
      if (e instanceof ApiError) {
        setPlacementError(`Place failed: HTTP ${e.status}`);
      } else {
        setPlacementError("Place failed.");
      }
    } finally {
      setBusyModel(null);
    }
  }

  function onPlacementDragEnd(event: DragEndEvent) {
    const model = String(event.active.id);
    const placement = event.over ? String(event.over.id) : null;
    if (!placement || !snapshot?.columns.includes(placement)) return;
    const current = snapshot.models.find((m) => m.name === model)?.config?.placement ?? "on_demand";
    if (current === placement || busyModel === model || !isAdmin) return;
    void onPlacementChange(model, placement);
  }

  async function onDelete(model: string) {
    if (!window.confirm(`Delete ${model} from Ollama? On-disk weights go away.`)) {
      return;
    }
    setBusyModel(model);
    try {
      await api(`/api/admin/ollama/models/${encodeURIComponent(model)}`, {
        method: "DELETE",
      });
    } catch (e) {
      if (e instanceof ApiError) {
        alert(`Delete failed: ${e.status}`);
      }
    } finally {
      setBusyModel(null);
    }
  }

  async function onPerfTest(model: string) {
    const started = Date.now();
    setPerfRun({
      model,
      status: "running",
      stage: "starting",
      elapsedMs: 0,
      tokensSoFar: null,
      tokensPerSec: null,
      lastEventAt: started,
      result: null,
      error: null,
      cancelling: false,
    });
    try {
      for await (const ev of streamSse(
        `/api/admin/ollama/models/${encodeURIComponent(model)}/perf-test`,
        { method: "POST", body: JSON.stringify({}) },
      )) {
        const receivedAt = Date.now();
        let data: Record<string, unknown> = {};
        try {
          data = ev.data ? JSON.parse(ev.data) : {};
        } catch {
          data = {};
        }
        setPerfRun((prev) => {
          if (!prev || prev.model !== model) return prev;
          if (ev.event === "stage") {
            return {
              ...prev,
              status: "running",
              stage: String(data.name ?? prev.stage),
              elapsedMs: 0,
              lastEventAt: receivedAt,
            };
          }
          if (ev.event === "progress" || ev.event === "heartbeat") {
            return {
              ...prev,
              status: "running",
              stage: String(data.stage ?? prev.stage),
              elapsedMs: Number(data.elapsed_ms ?? prev.elapsedMs),
              tokensSoFar:
                data.tokens_so_far === undefined ? prev.tokensSoFar : Number(data.tokens_so_far),
              tokensPerSec:
                data.tokens_per_sec === undefined ? prev.tokensPerSec : Number(data.tokens_per_sec),
              lastEventAt: receivedAt,
            };
          }
          if (ev.event === "result") {
            return {
              ...prev,
              status: "result",
              stage: "complete",
              result: data as PerfResult,
              lastEventAt: receivedAt,
              cancelling: false,
            };
          }
          if (ev.event === "cancelled") {
            return {
              ...prev,
              status: "cancelled",
              stage: "idle",
              elapsedMs: Number(data.elapsed_ms ?? prev.elapsedMs),
              result: null,
              lastEventAt: receivedAt,
              cancelling: false,
            };
          }
          if (ev.event === "error") {
            return {
              ...prev,
              status: "error",
              stage: String(data.stage ?? prev.stage),
              error: String(data.message ?? "Perf test failed"),
              lastEventAt: receivedAt,
              cancelling: false,
            };
          }
          return { ...prev, lastEventAt: receivedAt };
        });
      }
    } catch (e) {
      if (e instanceof ApiError) {
        setPerfRun((prev) =>
          prev && prev.model === model
            ? {
                ...prev,
                status: "error",
                error: `HTTP ${e.status}`,
                cancelling: false,
                lastEventAt: Date.now(),
              }
            : prev,
        );
      }
    }
  }

  async function onPerfTestAll(models: ModelCardPayload[]) {
    setTestAllBusy(true);
    try {
      for (const model of models) {
        await onPerfTest(model.name);
      }
    } finally {
      setTestAllBusy(false);
    }
  }

  async function onCancelPerfTest(model: string) {
    setPerfRun((prev) => (prev ? { ...prev, cancelling: true } : prev));
    try {
      const res = await api<{ cancelled: boolean }>(`/api/admin/ollama/models/${encodeURIComponent(model)}/perf-test/cancel`, {
        method: "POST",
      });
      if (!res.cancelled) {
        setPerfRun((prev) =>
          prev && prev.model === model
            ? {
                ...prev,
                status: "cancelled",
                stage: "idle",
                cancelling: false,
                lastEventAt: Date.now(),
              }
            : prev,
        );
      }
    } catch (e) {
      if (e instanceof ApiError) {
        setPerfRun((prev) =>
          prev
            ? {
                ...prev,
                status: "error",
                error: `Cancel failed: HTTP ${e.status}`,
                cancelling: false,
              }
            : prev,
        );
      }
    }
  }

  async function onRefreshMetadata() {
    setMetadataBusy(true);
    setPlacementError(null);
    try {
      await api("/api/admin/ollama/models/metadata/refresh", { method: "POST" });
      await refreshSnapshot();
    } catch (e) {
      setPlacementError(e instanceof ApiError ? `Metadata refresh failed: HTTP ${e.status}` : "Metadata refresh failed.");
    } finally {
      setMetadataBusy(false);
    }
  }

  if (!snapshot) {
    return (
      <>
        <AppHeader />
        <main className="flex-1 flex items-center justify-center text-neutral-500">
          Loading dashboard…
        </main>
      </>
    );
  }

  const isAdmin = me?.role === "admin";
  const buckets = new Map<string, ModelCardPayload[]>();
  for (const c of snapshot.columns) buckets.set(c, []);
  const visibleModels = snapshot.models.filter((m) => {
    const safe = m.context?.max_estimated_ctx;
    const configured = m.config?.num_ctx_default;
    const warning = Boolean(m.actual.mismatch || (safe && configured && configured > safe));
    if (filter === "loaded") return m.actual.loaded;
    if (filter === "missing_perf") return !m.metrics;
    if (filter === "warnings") return warning;
    return true;
  });
  for (const m of visibleModels) {
    const placement = m.config?.placement ?? "on_demand";
    if (buckets.has(placement)) {
      buckets.get(placement)!.push(m);
    } else {
      buckets.get("on_demand")?.push(m);
    }
  }

  return (
    <>
      <AppHeader />
      <main className="cockpit-page flex-1 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-neutral-950 dark:text-white">
              Operations
            </h1>
            <p className="text-sm text-neutral-600 dark:text-neutral-400">
              Live Ollama state, GPU pressure, and model placement.
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-md border border-[var(--cockpit-border)] bg-[var(--cockpit-surface)] p-1">
          <button
            onClick={() => setTab("live")}
            className={`px-3 py-1.5 rounded-md text-sm font-medium ${
              tab === "live"
                ? "bg-neutral-950 text-white dark:bg-white dark:text-neutral-950"
                : "text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800"
            }`}
          >
            Live
          </button>
          <button
            onClick={() => setTab("history")}
            className={`px-3 py-1.5 rounded-md text-sm font-medium ${
              tab === "history"
                ? "bg-neutral-950 text-white dark:bg-white dark:text-neutral-950"
                : "text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800"
            }`}
          >
            History
          </button>
          </div>
        </div>

        {tab === "live" ? (
          <>
            <section className="cockpit-panel p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <StatusPill status={snapshot.status} />
                  <span className="text-xs text-neutral-500 dark:text-neutral-400">
                    Snapshot {new Date(snapshot.ts).toLocaleTimeString()}
                  </span>
                </div>
                {placementError ? (
                  <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-1 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300">
                    {placementError}
                  </div>
                ) : null}
              </div>
              <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
                {snapshot.gpus.length === 0 ? (
                  <span className="text-sm text-neutral-500 dark:text-neutral-400">
                    No GPU telemetry detected (Mac / CPU-only / nvidia-smi missing).
                  </span>
                ) : (
                  snapshot.gpus.map((g) => (
                    <GpuStripItem key={g.index} g={g} />
                  ))
                )}
              </div>
            </section>

            <DndContext sensors={sensors} onDragEnd={onPlacementDragEnd}>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-1 rounded-md border border-[var(--cockpit-border)] bg-[var(--cockpit-surface)] p-1">
                  {(["all", "loaded", "missing_perf", "warnings"] as ModelFilter[]).map((f) => (
                    <button
                      key={f}
                      type="button"
                      onClick={() => setFilter(f)}
                      className={`rounded px-2.5 py-1 text-xs font-medium ${
                        filter === f
                          ? "bg-neutral-950 text-white dark:bg-white dark:text-neutral-950"
                          : "text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800"
                      }`}
                    >
                      {f === "missing_perf" ? "Missing perf" : f[0].toUpperCase() + f.slice(1)}
                    </button>
                  ))}
                </div>
                {isAdmin ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setLoadModelOpen(true)}
                      className="cockpit-button min-h-8 px-3 py-1.5 text-xs"
                    >
                      Load model
                    </button>
                    <button
                      type="button"
                      onClick={() => onPerfTestAll(visibleModels)}
                      disabled={!visibleModels.length || Boolean(perfRun) || testAllBusy}
                      className="cockpit-button min-h-8 px-3 py-1.5 text-xs"
                    >
                      {testAllBusy ? "Testing" : "Test all"}
                    </button>
                    <button
                      type="button"
                      onClick={onRefreshMetadata}
                      disabled={metadataBusy}
                      className="cockpit-button min-h-8 px-3 py-1.5 text-xs"
                    >
                      {metadataBusy ? "Refreshing" : "Refresh metadata"}
                    </button>
                  </div>
                ) : null}
              </div>
              <section className="flex gap-3 overflow-x-auto pb-2">
                {snapshot.columns.map((col) => (
                  <ColumnView
                    key={col}
                    col={col}
                    models={buckets.get(col) ?? []}
                    columns={snapshot.columns}
                    isAdmin={!!isAdmin}
                    busyModel={busyModel}
                    onPlacementChange={onPlacementChange}
                    onDelete={onDelete}
                    onPerfTest={onPerfTest}
                  />
                ))}
              </section>
            </DndContext>
          </>
        ) : (
          <DashboardHistory />
        )}
      </main>

      {perfRun ? (
        <PerfDrawer
          run={perfRun}
          now={perfTick}
          onCancel={() => onCancelPerfTest(perfRun.model)}
          onClose={() => setPerfRun(null)}
          onRunAgain={() => onPerfTest(perfRun.model)}
        />
      ) : null}
      {loadModelOpen && isAdmin ? (
        <LoadModelDialog
          snapshot={snapshot}
          onClose={() => setLoadModelOpen(false)}
          onPulled={refreshSnapshot}
        />
      ) : null}
    </>
  );
}

function StatusPill({ status }: { status: string }) {
  const label = status === "ollama_unreachable" ? "Ollama unreachable" : status.replace(/_/g, " ");
  const bg =
    status === "healthy"
      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200"
      : status === "ollama_unreachable"
        ? "bg-rose-100 text-rose-800 dark:bg-rose-900 dark:text-rose-200"
        : "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200";
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wide ${bg}`}>
      {label}
    </span>
  );
}

function LoadModelDialog({
  snapshot,
  onClose,
  onPulled,
}: {
  snapshot: DashboardSnapshot;
  onClose: () => void;
  onPulled: () => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [pulling, setPulling] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const modelName = query.trim();
  const installedNames = useMemo(() => new Set(snapshot.models.map((m) => m.name)), [snapshot.models]);
  const onDemandModels = useMemo(
    () =>
      snapshot.models
        .filter((m) => (m.config?.placement ?? "on_demand") === "on_demand")
        .sort((a, b) => a.name.localeCompare(b.name)),
    [snapshot.models],
  );
  const visibleModels = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return onDemandModels;
    return onDemandModels.filter((m) => m.name.toLowerCase().includes(needle));
  }, [onDemandModels, query]);
  const exactInstalled = modelName ? installedNames.has(modelName) : false;

  async function onDownload() {
    if (!modelName || pulling || exactInstalled) return;
    setPulling(true);
    setStatus("Starting download...");
    setError(null);
    try {
      for await (const ev of streamSse(`/api/admin/ollama/models/${encodeURIComponent(modelName)}/pull`, {
        method: "POST",
      })) {
        let data: Record<string, unknown> = {};
        try {
          data = ev.data ? JSON.parse(ev.data) : {};
        } catch {
          data = {};
        }
        if (ev.event === "progress") {
          const label = typeof data.status === "string" ? data.status : "Downloading";
          const completed = typeof data.completed === "number" ? data.completed : null;
          const total = typeof data.total === "number" ? data.total : null;
          setStatus(total && completed != null ? `${label} (${Math.round((completed / total) * 100)}%)` : label);
        }
        if (ev.event === "error") {
          setError(typeof data.detail === "string" ? data.detail : "Download failed.");
          break;
        }
        if (ev.event === "done") {
          const success = data.success === true;
          setStatus(success ? "Downloaded. Refreshing dashboard..." : "Download finished without success.");
          if (success) {
            await onPulled();
            setQuery("");
          }
          break;
        }
      }
    } catch (e) {
      setError(e instanceof ApiError ? `Download failed: HTTP ${e.status}` : "Download failed.");
    } finally {
      setPulling(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4">
      <section className="w-full max-w-2xl rounded-md border border-[var(--cockpit-border)] bg-[var(--cockpit-surface)] shadow-2xl">
        <div className="flex items-start justify-between gap-4 border-b border-[var(--cockpit-border)] px-5 py-4">
          <div>
            <h2 className="text-lg font-semibold text-neutral-950 dark:text-white">Load model</h2>
            <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">
              Search local On Demand models or download a new Ollama model by exact name.
            </p>
          </div>
          <button type="button" onClick={onClose} className="cockpit-button px-3 py-1.5 text-sm">
            Close
          </button>
        </div>
        <div className="space-y-4 px-5 py-4">
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              className="cockpit-input min-h-10 flex-1"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="llama3.1:8b"
              autoFocus
            />
            <button
              type="button"
              onClick={onDownload}
              disabled={!modelName || pulling || exactInstalled}
              className="cockpit-button cockpit-button-primary min-h-10 px-4 text-sm"
            >
              {pulling ? "Downloading" : exactInstalled ? "Already present" : "Download"}
            </button>
          </div>
          {status || error ? (
            <div
              className={`rounded-md border px-3 py-2 text-sm ${
                error
                  ? "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900 dark:bg-rose-950/40 dark:text-rose-300"
                  : "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-200"
              }`}
            >
              {error ?? status}
            </div>
          ) : null}
          <div>
            <div className="mb-2 flex items-center justify-between gap-2">
              <h3 className="text-xs font-semibold uppercase text-neutral-600 dark:text-neutral-400">
                On Demand
              </h3>
              <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-mono text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
                {visibleModels.length}
              </span>
            </div>
            <div className="max-h-80 overflow-y-auto rounded-md border border-[var(--cockpit-border)]">
              {visibleModels.length ? (
                <ul className="divide-y divide-[var(--cockpit-border)]">
                  {visibleModels.map((m) => (
                    <li key={m.name} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                      <div className="min-w-0">
                        <div className="break-all font-medium text-neutral-900 dark:text-neutral-100">
                          {m.name}
                        </div>
                        <div className="text-xs text-neutral-500 dark:text-neutral-400">
                          {m.tag ?? "untagged"} · {fmtBytes(m.size_bytes)}
                        </div>
                      </div>
                      <span className="shrink-0 rounded bg-neutral-100 px-2 py-1 text-[11px] text-neutral-600 dark:bg-neutral-900 dark:text-neutral-300">
                        On Demand
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="px-3 py-8 text-center text-sm text-neutral-500 dark:text-neutral-400">
                  No local On Demand models match.
                </div>
              )}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

function GpuStripItem({
  g,
}: {
  g: {
    index: number;
    vram_used_mb: number;
    vram_total_mb: number;
    temp_c: number | null;
    power_w: number | null;
    max_power_w: number | null;
  };
}) {
  const usedGb = g.vram_used_mb / 1024;
  const totalGb = g.vram_total_mb / 1024;
  const tempStatus = gpuTempStatus(g.temp_c);
  const wattsCls = wattsColor(g.power_w, g.max_power_w ?? DEFAULT_TDP_W);
  return (
    <div className="rounded-md border border-[var(--cockpit-border)] bg-[var(--cockpit-surface)] p-3 text-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="font-semibold">GPU {g.index}</div>
        {tempStatus ? (
          <span
            className={`px-1.5 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wide ${tempStatus.cls}`}
            title="RTX 3090 thresholds: ≤70 Good · 71–82 Workload · 83–89 Throttling · ≥90 Critical"
          >
            {tempStatus.label}
          </span>
        ) : null}
      </div>
      <div className="text-xs text-neutral-600 dark:text-neutral-400 mt-1">
        {usedGb.toFixed(1)} / {totalGb.toFixed(1)} GB VRAM
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
        <div
          className="h-full rounded-full bg-emerald-500"
          style={{ width: `${Math.min(100, (usedGb / Math.max(totalGb, 1)) * 100)}%` }}
        />
      </div>
      {g.temp_c != null ? (
        <div className="mt-2 flex items-center gap-2 text-xs">
          <span className="font-mono text-neutral-700 dark:text-neutral-300">
            {Math.round(g.temp_c)}°C
          </span>
          {g.power_w != null ? (
            <span className={`font-mono ${wattsCls}`}>
              {g.power_w.toFixed(0)} W / {g.max_power_w ?? DEFAULT_TDP_W} W
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function crossGpuEvictionWarning(snapshot: DashboardSnapshot, movedModel: string): string | null {
  const impacted = snapshot.models
    .filter((m) => m.name !== movedModel)
    .filter((m) => /^gpu\d+$/.test(m.config?.placement ?? ""))
    .filter((m) => m.actual.loaded || m.config.keep_alive_mode !== "unload");
  if (!impacted.length) return null;

  const byGpu = new Map<string, string[]>();
  for (const model of impacted) {
    const placement = model.config?.placement ?? "unknown";
    const label = COLUMN_LABELS[placement] ?? placement.toUpperCase();
    const suffix = model.actual.loaded ? "" : " (configured warm)";
    byGpu.set(label, [...(byGpu.get(label) ?? []), `${model.name}${suffix}`]);
  }
  const affected = Array.from(byGpu.entries())
    .map(([gpu, names]) => `${gpu}: ${names.join(", ")}`)
    .join("\n");

  return [
    `Move ${movedModel} to Cross GPU?`,
    "",
    "Cross GPU can reserve VRAM on every GPU. Ollama may unload currently warm single-GPU models to make room.",
    "",
    "Potentially affected:",
    affected,
    "",
    "Continue?",
  ].join("\n");
}

function ColumnView(props: {
  col: string;
  models: ModelCardPayload[];
  columns: string[];
  isAdmin: boolean;
  busyModel: string | null;
  onPlacementChange: (
    model: string,
    placement: string,
    extras?: { keep_alive_mode?: string; keep_alive_seconds?: number; num_ctx_default?: number | null },
  ) => void;
  onDelete: (model: string) => void;
  onPerfTest: (model: string) => void;
}) {
  const { col, models, columns, isAdmin, busyModel } = props;
  const { isOver, setNodeRef } = useDroppable({ id: col, disabled: !isAdmin });
  const label = COLUMN_LABELS[col] ?? col.toUpperCase();
  const warm = isWarmColumn(col);
  return (
    <section
      ref={setNodeRef}
      className={`min-h-64 w-[280px] flex-none rounded-md border p-2 ${
        isOver
          ? "border-sky-400 bg-sky-50 dark:border-sky-500 dark:bg-sky-950/40"
          : warm
            ? "border-emerald-200 dark:border-emerald-900 bg-emerald-50/45 dark:bg-emerald-950/20"
            : "border-[var(--cockpit-border)] bg-[var(--cockpit-surface)]"
      }`}
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-xs font-semibold uppercase text-neutral-600 dark:text-neutral-400">
          {label}
        </h2>
        <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-mono text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
          {models.length}
        </span>
      </div>
      {models.length === 0 ? (
        <div className="flex min-h-40 items-center justify-center rounded-md border border-dashed border-neutral-300 text-xs text-neutral-400 dark:border-neutral-700">
          Empty
        </div>
      ) : (
        <ul className="space-y-2">
          {models.map((m) => (
            <ModelCardView
              key={m.name}
              m={m}
              columns={columns}
              isAdmin={isAdmin}
              busy={busyModel === m.name}
              onPlacementChange={(placement, extras) => props.onPlacementChange(m.name, placement, extras)}
              onDelete={() => props.onDelete(m.name)}
              onPerfTest={() => props.onPerfTest(m.name)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function ModelCardView({
  m,
  columns,
  isAdmin,
  busy,
  onPlacementChange,
  onDelete,
  onPerfTest,
}: {
  m: ModelCardPayload;
  columns: string[];
  isAdmin: boolean;
  busy: boolean;
  onPlacementChange: (
    placement: string,
    extras?: { keep_alive_mode?: string; keep_alive_seconds?: number; num_ctx_default?: number | null },
  ) => void;
  onDelete: () => void;
  onPerfTest: () => void;
}) {
  const placement = m.config?.placement ?? "on_demand";
  const safeCtx = m.context?.max_estimated_ctx ?? null;
  const configuredCtx = m.config?.num_ctx_default ?? null;
  const measuredCtx = m.context?.max_measured_ctx ?? m.metrics?.max_ctx_observed ?? null;
  const ctxWarning = Boolean(safeCtx && configuredCtx && configuredCtx > safeCtx);
  const metadataBits = [m.metadata?.parameter_size, m.metadata?.quantization_level].filter(Boolean);
  const actualLabel =
    m.actual.gpu_layout && Object.keys(m.actual.gpu_layout).length
      ? Object.entries(m.actual.gpu_layout)
          .map(([gpu, mb]) => `${gpu}:${Math.round(mb / 1024)}GB`)
          .join(" ")
      : m.actual.main_gpu_actual != null
        ? `GPU ${m.actual.main_gpu_actual}`
        : m.actual.loaded
          ? "loaded"
          : "idle";
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: m.name,
    disabled: !isAdmin || busy,
  });
  const dragStyle = transform
    ? {
        transform: `translate3d(${transform.x}px, ${transform.y}px, 0)`,
        zIndex: 30,
      }
    : undefined;
  return (
    <li
      ref={setNodeRef}
      style={dragStyle}
      className={`rounded-md border border-[var(--cockpit-border)] bg-[var(--cockpit-surface)] p-3 text-sm shadow-sm ${
        isDragging ? "opacity-80 shadow-lg" : ""
      } ${busy ? "opacity-70" : ""}`}
    >
      <div className="flex items-start gap-2">
        {isAdmin ? (
          <button
            type="button"
            aria-label={`Drag ${m.name}`}
            title="Drag to place"
            disabled={busy}
            className="mt-0.5 flex h-6 w-6 flex-shrink-0 cursor-grab items-center justify-center rounded border border-neutral-200 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700 active:cursor-grabbing disabled:cursor-not-allowed dark:border-neutral-700 dark:hover:bg-neutral-800 dark:hover:text-neutral-200"
            {...listeners}
            {...attributes}
          >
            ::
          </button>
        ) : null}
        <div className="min-w-0 flex-1">
          <div className="font-semibold break-all leading-snug">{m.name}</div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-neutral-600 dark:text-neutral-400">
            <span className="rounded bg-neutral-100 px-1.5 py-0.5 dark:bg-neutral-900">
              {m.tag ?? "untagged"}
            </span>
            <span>{fmtBytes(m.size_bytes)}</span>
            {metadataBits.length ? <span>{metadataBits.join(" · ")}</span> : null}
          </div>
          <div className="mt-1 text-[11px] text-neutral-500 dark:text-neutral-400">
            {m.metadata?.release_date_label ?? "Release: unknown"}
          </div>
        </div>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-1 text-[11px]">
        <div className="rounded bg-neutral-100 px-2 py-1 dark:bg-neutral-900">
          <span className="text-neutral-500 dark:text-neutral-400">req</span>{" "}
          {COLUMN_LABELS[placement] ?? placement.toUpperCase()}
        </div>
        <div className={`rounded px-2 py-1 ${m.actual.loaded ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300" : "bg-neutral-100 dark:bg-neutral-900"}`}>
          {actualLabel}
        </div>
        <div className="rounded bg-neutral-100 px-2 py-1 dark:bg-neutral-900">
          keep {m.config?.keep_alive_label ?? "Default"}
        </div>
        <div className={`rounded px-2 py-1 ${ctxWarning ? "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300" : "bg-neutral-100 dark:bg-neutral-900"}`}>
          ctx {configuredCtx?.toLocaleString() ?? "—"} / {safeCtx?.toLocaleString() ?? "?"}
        </div>
      </div>
      {m.actual.mismatch ? (
        <div className="text-xs text-rose-600 mt-0.5">
          Requested {placement} · Ollama placed on GPU {m.actual.main_gpu_actual}
        </div>
      ) : null}
      {ctxWarning ? (
        <div className="mt-1 text-xs text-amber-700 dark:text-amber-300">
          Configured ctx exceeds safe estimate.
        </div>
      ) : null}
      {m.metrics ? (
        <div className="mt-2 grid grid-cols-3 gap-1 text-xs font-mono">
          <div className="rounded bg-neutral-100 px-2 py-1 dark:bg-neutral-900">
            {m.metrics.cold_load_seconds?.toFixed(1) ?? "—"}s
          </div>
          <div className="rounded bg-neutral-100 px-2 py-1 dark:bg-neutral-900">
            {m.metrics.throughput_tps?.toFixed(1) ?? "—"}tps
          </div>
          <div className="rounded bg-neutral-100 px-2 py-1 dark:bg-neutral-900">
            {measuredCtx ?? "?"} ctx
          </div>
        </div>
      ) : (
        <div className="text-xs text-neutral-500 italic mt-1">
          no perf data — run Test performance
        </div>
      )}
      {isAdmin ? (
        <div className="flex flex-wrap gap-1.5 mt-3">
          <select
            className="cockpit-input min-h-7 text-xs"
            value={placement}
            disabled={busy}
            onChange={(e) => onPlacementChange(e.target.value)}
          >
            {columns.map((c) => (
              <option key={c} value={c}>
                {COLUMN_LABELS[c] ?? c}
              </option>
            ))}
          </select>
          <select
            className="cockpit-input min-h-7 text-xs"
            value={
              m.config.keep_alive_mode === "permanent"
                ? "permanent"
                : m.config.keep_alive_seconds === 900
                  ? "15m"
                  : m.config.keep_alive_seconds === 3600
                    ? "1h"
                    : m.config.keep_alive_seconds === 14400
                      ? "4h"
                      : m.config.keep_alive_seconds === 86400
                        ? "24h"
                        : m.config.keep_alive_mode === "finite"
                          ? "custom"
                          : "default"
            }
            disabled={busy}
            onChange={(e) => {
              const v = e.target.value;
              if (v === "permanent") onPlacementChange(placement, { keep_alive_mode: "permanent" });
              if (v === "15m") onPlacementChange(placement, { keep_alive_mode: "finite", keep_alive_seconds: 900 });
              if (v === "1h") onPlacementChange(placement, { keep_alive_mode: "finite", keep_alive_seconds: 3600 });
              if (v === "4h") onPlacementChange(placement, { keep_alive_mode: "finite", keep_alive_seconds: 14400 });
              if (v === "24h") onPlacementChange(placement, { keep_alive_mode: "finite", keep_alive_seconds: 86400 });
            }}
            title="Keep alive"
          >
            <option value="default">Default</option>
            <option value="15m">15m</option>
            <option value="1h">1h</option>
            <option value="4h">4h</option>
            <option value="24h">24h</option>
            <option value="permanent">Permanent</option>
            <option value="custom">Custom</option>
          </select>
          <button
            type="button"
            disabled={busy}
            onClick={onPerfTest}
            className="cockpit-button min-h-7 px-2 py-1 text-xs"
          >
            Perf
          </button>
          <button
            type="button"
            disabled={busy || !safeCtx}
            onClick={() => onPlacementChange(placement, { num_ctx_default: safeCtx })}
            className="cockpit-button min-h-7 px-2 py-1 text-xs"
            title="Use safe estimate"
          >
            Safe ctx
          </button>
          <button
            type="button"
            disabled={busy || !measuredCtx}
            onClick={() => onPlacementChange(placement, { num_ctx_default: measuredCtx })}
            className="cockpit-button min-h-7 px-2 py-1 text-xs"
            title="Use measured max"
          >
            Measured
          </button>
          <button
            type="button"
            disabled={busy || configuredCtx == null}
            onClick={() => onPlacementChange(placement, { num_ctx_default: null })}
            className="cockpit-button min-h-7 px-2 py-1 text-xs"
            title="Clear context override"
          >
            Clear ctx
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onDelete}
            className="cockpit-button min-h-7 border-rose-300 px-2 py-1 text-xs text-rose-700 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-300 dark:hover:bg-rose-950"
          >
            Delete
          </button>
        </div>
      ) : null}
    </li>
  );
}

const PERF_STAGE_LABELS: Record<string, string> = {
  starting: "Starting",
  lock: "Waiting for lock",
  unload: "Unload",
  cold_load: "Cold load",
  throughput: "Throughput run",
  context_probe: "Context probe",
  persist: "Persist",
  restore: "Restore",
  complete: "Complete",
  idle: "Idle",
};

function fmtElapsed(ms: number) {
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function PerfDrawer({
  run,
  now,
  onCancel,
  onClose,
  onRunAgain,
}: {
  run: PerfRunState;
  now: number;
  onCancel: () => void;
  onClose: () => void;
  onRunAgain: () => void;
}) {
  const running = run.status === "running";
  const secondsSinceEvent = Math.max(0, (now - run.lastEventAt) / 1000);
  const stalled = running && secondsSinceEvent >= 2;
  const stageLabel = PERF_STAGE_LABELS[run.stage] ?? run.stage.replace(/_/g, " ");
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40 backdrop-blur-sm">
      <aside className="h-full w-full max-w-xl overflow-y-auto border-l border-[var(--cockpit-border)] bg-[var(--cockpit-surface)] p-5 shadow-2xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">Performance test</h2>
            <p className="text-sm text-neutral-600 dark:text-neutral-400 break-all mt-1">
              {run.model}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="cockpit-button"
          >
            Close
          </button>
        </div>

        <div className="cockpit-panel mt-5 p-4 space-y-4">
          <div className="flex items-center justify-between gap-3">
            <span
              className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold uppercase ${
                run.status === "error"
                  ? "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-200"
                  : run.status === "cancelled"
                    ? "bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-200"
                    : run.status === "result"
                      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200"
                      : "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200"
              }`}
            >
              {stageLabel}
            </span>
            <span className="font-mono text-sm text-neutral-700 dark:text-neutral-300">
              {fmtElapsed(run.elapsedMs)}
            </span>
          </div>

          {run.tokensPerSec !== null ? (
            <div className="grid grid-cols-2 gap-3">
              <MetricTile
                label="Tokens so far"
                value={run.tokensSoFar === null ? "—" : run.tokensSoFar.toLocaleString()}
              />
              <MetricTile label="Live tokens/s" value={run.tokensPerSec.toFixed(1)} />
            </div>
          ) : (
            <p className="text-sm text-neutral-500">
              Throughput appears here once the throughput run starts.
            </p>
          )}

          {stalled ? (
            <div className="rounded-md border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 p-3 text-sm text-amber-900 dark:text-amber-100">
              Stalled — last event {secondsSinceEvent.toFixed(1)}s ago, last phase:{" "}
              {stageLabel}
            </div>
          ) : null}

          {run.status === "error" ? (
            <div className="rounded-md border border-rose-300 dark:border-rose-800 bg-rose-50 dark:bg-rose-950/40 p-3 text-sm text-rose-900 dark:text-rose-100">
              {run.error}
            </div>
          ) : null}

          {run.status === "cancelled" ? (
            <div className="rounded-md border border-neutral-200 dark:border-neutral-800 bg-neutral-50 dark:bg-neutral-900 p-3 text-sm text-neutral-700 dark:text-neutral-300">
              Cancelled. The drawer is idle and no partial metrics were saved.
            </div>
          ) : null}

          {run.status === "result" && run.result ? (
            <div className="rounded-md border border-emerald-200 dark:border-emerald-900 bg-emerald-50 dark:bg-emerald-950/30 p-3 space-y-3">
              <div className="grid grid-cols-3 gap-2">
                <MetricTile
                  label="Cold load"
                  value={
                    run.result.cold_load_seconds == null
                      ? "—"
                      : `${run.result.cold_load_seconds.toFixed(1)} s`
                  }
                />
                <MetricTile
                  label="Throughput"
                  value={
                    run.result.throughput_tps == null
                      ? "—"
                      : `${run.result.throughput_tps.toFixed(1)} tps`
                  }
                />
                <MetricTile
                  label="Max ctx"
                  value={run.result.max_ctx_observed?.toLocaleString() ?? "—"}
                />
              </div>
              <div>
                <div className="text-xs font-semibold uppercase text-neutral-500 mb-1">
                  GPU layout diff
                </div>
                <pre className="text-xs font-mono rounded bg-white/70 dark:bg-neutral-950/70 p-2 overflow-x-auto">
                  {JSON.stringify(run.result.gpu_layout_diff ?? {}, null, 2)}
                </pre>
              </div>
            </div>
          ) : null}

          <div className="flex items-center justify-end gap-2">
            {running ? (
              <button
                type="button"
                onClick={onCancel}
                disabled={run.cancelling}
                className="rounded-md border border-rose-300 dark:border-rose-800 px-3 py-1.5 text-sm font-medium text-rose-700 dark:text-rose-300 hover:bg-rose-50 dark:hover:bg-rose-950 disabled:opacity-60"
              >
                {run.cancelling ? "Cancelling…" : "Cancel"}
              </button>
            ) : (
              <button
                type="button"
                onClick={onRunAgain}
                className="cockpit-button"
              >
                Run again
              </button>
            )}
          </div>
        </div>
      </aside>
    </div>
  );
}

function MetricTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-[var(--cockpit-border)] bg-[var(--cockpit-surface)] p-2">
      <div className="text-[11px] uppercase font-semibold text-neutral-500">{label}</div>
      <div className="font-mono text-sm mt-1">{value}</div>
    </div>
  );
}
