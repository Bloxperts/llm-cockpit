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
  GpuPayload,
  ModelCardPayload,
  fmtBytes,
  isWarmColumn,
} from "@/lib/dashboard-types";

type DashboardTab = "live" | "benchmarks" | "history";
type PerfStatus = "idle" | "running" | "result" | "cancelled" | "error";
type ModelFilter = "all" | "loaded" | "missing_perf" | "warnings";

type PerfResult = {
  cold_load_seconds?: number | null;
  warm_load_seconds?: number | null;
  throughput_tps?: number | null;
  max_ctx_observed?: number | null;
  benchmark_profile?: string | null;
  placement_tested?: string | null;
  gpu_layout_diff?: Record<string, number>;
  notes?: string | null;
  profiles?: PerfResult[];
};

type PerfRunState = {
  model: string;
  profiles: string[];
  currentProfile: string | null;
  completedProfiles: PerfResult[];
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

type CatalogModel = {
  name: string;
  description?: string | null;
  sizes?: string[];
  capabilities?: string[];
  pulls?: string | null;
  tags?: string | null;
  updated?: string | null;
  url?: string | null;
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
    if (snapshot && placement !== current) {
      const warning = placementEvictionWarning(snapshot, model, placement);
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
        setPlacementError(apiErrorSummary("Place failed", e));
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

  async function onPerfTest(model: string, profiles: string[] = []) {
    const started = Date.now();
    setPerfRun({
      model,
      profiles,
      currentProfile: profiles[0] ?? null,
      completedProfiles: [],
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
        { method: "POST", body: JSON.stringify(profiles.length ? { profiles } : {}) },
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
          if (ev.event === "profile") {
            return {
              ...prev,
              status: "running",
              currentProfile: String(data.profile ?? prev.currentProfile ?? ""),
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
            const result = data as PerfResult;
            return {
              ...prev,
              status: "result",
              stage: "complete",
              result,
              completedProfiles: result.profiles ?? prev.completedProfiles,
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
      await refreshSnapshot();
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
          <button
            onClick={() => setTab("benchmarks")}
            className={`px-3 py-1.5 rounded-md text-sm font-medium ${
              tab === "benchmarks"
                ? "bg-neutral-950 text-white dark:bg-white dark:text-neutral-950"
                : "text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800"
            }`}
          >
            Benchmarks
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
                    gpus={snapshot.gpus}
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
        ) : tab === "benchmarks" ? (
          <BenchmarkDecisionView
            snapshot={snapshot}
            perfRun={perfRun}
            onRetest={(model, profile) => onPerfTest(model, [profile])}
          />
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
  const [catalog, setCatalog] = useState<CatalogModel[]>([]);
  const [catalogBusy, setCatalogBusy] = useState(false);
  const [pullingModel, setPullingModel] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const installedNames = useMemo(() => new Set(snapshot.models.map((m) => m.name)), [snapshot.models]);
  const installedBases = useMemo(
    () => new Set(snapshot.models.map((m) => m.name.split(":", 1)[0])),
    [snapshot.models],
  );

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setCatalogBusy(true);
      setError(null);
      try {
        const params = new URLSearchParams({ q: query.trim(), limit: "30" });
        const response = await api<{ models: CatalogModel[] }>(`/api/admin/ollama/models/catalog?${params}`);
        if (!cancelled) setCatalog(response.models ?? []);
      } catch (e) {
        if (!cancelled) {
          setCatalog([]);
          setError(e instanceof ApiError ? `Catalog search failed: HTTP ${e.status}` : "Catalog search failed.");
        }
      } finally {
        if (!cancelled) setCatalogBusy(false);
      }
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  async function onDownload(modelName: string) {
    if (!modelName || pullingModel) return;
    setPullingModel(modelName);
    setStatus(`Starting download for ${modelName}...`);
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
          setStatus(total && completed != null ? `${modelName}: ${label} (${Math.round((completed / total) * 100)}%)` : `${modelName}: ${label}`);
        }
        if (ev.event === "error") {
          setError(typeof data.detail === "string" ? data.detail : "Download failed.");
          break;
        }
        if (ev.event === "done") {
          const success = data.success === true;
          setStatus(success ? `${modelName} downloaded. Refreshing dashboard...` : "Download finished without success.");
          if (success) {
            await onPulled();
            setCatalog((prev) => prev.filter((m) => m.name !== modelName));
          }
          break;
        }
      }
    } catch (e) {
      setError(e instanceof ApiError ? `Download failed: HTTP ${e.status}` : "Download failed.");
    } finally {
      setPullingModel(null);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4">
      <section className="w-full max-w-2xl rounded-md border border-[var(--cockpit-border)] bg-[var(--cockpit-surface)] shadow-2xl">
        <div className="flex items-start justify-between gap-4 border-b border-[var(--cockpit-border)] px-5 py-4">
          <div>
            <h2 className="text-lg font-semibold text-neutral-950 dark:text-white">Load model</h2>
            <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">
              Search Ollama&apos;s model catalog. Local models are hidden from this list.
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
              placeholder="Search Ollama catalog"
              autoFocus
            />
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
                Ollama Catalog
              </h3>
              <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-mono text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
                {catalogBusy ? "..." : catalog.length}
              </span>
            </div>
            <div className="max-h-80 overflow-y-auto rounded-md border border-[var(--cockpit-border)]">
              {catalog.length ? (
                <ul className="divide-y divide-[var(--cockpit-border)]">
                  {catalog.map((m) => {
                    const installed = installedNames.has(m.name) || installedBases.has(m.name.split(":", 1)[0]);
                    return (
                    <li key={m.name} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                      <div className="min-w-0">
                        <div className="break-all font-medium text-neutral-900 dark:text-neutral-100">
                          {m.name}
                        </div>
                        {m.description ? (
                          <div className="mt-0.5 max-h-9 overflow-hidden text-xs text-neutral-600 dark:text-neutral-400">
                            {m.description}
                          </div>
                        ) : null}
                        <div className="mt-1 flex flex-wrap gap-1 text-[11px] text-neutral-500 dark:text-neutral-400">
                          {(m.sizes ?? []).slice(0, 6).map((size) => (
                            <span key={size} className="rounded bg-neutral-100 px-1.5 py-0.5 dark:bg-neutral-900">
                              {size}
                            </span>
                          ))}
                          {(m.capabilities ?? []).slice(0, 4).map((capability) => (
                            <span key={capability} className="rounded bg-sky-50 px-1.5 py-0.5 text-sky-700 dark:bg-sky-950 dark:text-sky-300">
                              {capability}
                            </span>
                          ))}
                          {m.pulls ? <span>{m.pulls} pulls</span> : null}
                          {m.tags ? <span>{m.tags} tags</span> : null}
                          {m.updated ? <span>updated {m.updated}</span> : null}
                        </div>
                      </div>
                      <button
                        type="button"
                        disabled={Boolean(pullingModel) || installed}
                        onClick={() => onDownload(m.name)}
                        className="cockpit-button cockpit-button-primary min-h-8 shrink-0 px-3 py-1.5 text-xs"
                      >
                        {pullingModel === m.name ? "Downloading" : installed ? "Installed" : "Download"}
                      </button>
                    </li>
                  );
                  })}
                </ul>
              ) : catalogBusy ? (
                <div className="px-3 py-8 text-center text-sm text-neutral-500 dark:text-neutral-400">
                  Searching Ollama catalog...
                </div>
              ) : (
                <div className="px-3 py-8 text-center text-sm text-neutral-500 dark:text-neutral-400">
                  No catalog models match, or matching models are already installed.
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

function placementEvictionWarning(snapshot: DashboardSnapshot, movedModel: string, placement: string): string | null {
  if (placement === "multi_gpu") return crossGpuEvictionWarning(snapshot, movedModel);
  if (!/^gpu\d+$/.test(placement)) return null;
  const gpu = Number(placement.slice(3));
  const target = snapshot.gpus.find((g) => g.index === gpu);
  const model = snapshot.models.find((m) => m.name === movedModel);
  if (!target || !model) {
    return [
      `Move ${movedModel} to ${placement.toUpperCase()}?`,
      "",
      "GPU capacity is uncertain because telemetry is incomplete. Ollama may unload existing models if it needs VRAM.",
      "",
      "Continue?",
    ].join("\n");
  }
  const freeMb = target.vram_total_mb - target.vram_used_mb;
  const modelSizeMb = model.size_bytes / 1024 / 1024;
  const residents = snapshot.models
    .filter((m) => m.name !== movedModel)
    .filter((m) => m.config?.placement === placement)
    .filter((m) => m.actual.loaded || m.config.keep_alive_mode !== "unload")
    .map((m) => `${m.name}${m.actual.loaded ? "" : " (configured warm)"}`);
  if (modelSizeMb <= freeMb * 0.85 && residents.length === 0) return null;
  return [
    `Move ${movedModel} to ${placement.toUpperCase()}?`,
    "",
    `Free VRAM on ${placement.toUpperCase()} is about ${(freeMb / 1024).toFixed(1)} GB; model size is ${fmtBytes(model.size_bytes)} before runtime overhead.`,
    residents.length ? "Potentially affected warm models:" : "No warm resident model is known, but the fit estimate is tight.",
    residents.join(", "),
    "",
    "Ollama can unload existing models when VRAM is insufficient. Continue?",
  ].filter(Boolean).join("\n");
}

type BenchmarkSort = "score" | "model" | "profile" | "tps" | "ctx" | "tested";
type RecommendationUseCase = "chat" | "code" | "large_context" | "multi_gpu";

function BenchmarkDecisionView({
  snapshot,
  perfRun,
  onRetest,
}: {
  snapshot: DashboardSnapshot;
  perfRun: PerfRunState | null;
  onRetest: (model: string, profile: string) => void;
}) {
  const [sort, setSort] = useState<BenchmarkSort>("score");
  const [useCase, setUseCase] = useState<RecommendationUseCase>("chat");
  const rows = useMemo(() => {
    const out = snapshot.models.flatMap((model) => {
      const profiles = model.benchmark_profiles?.length
        ? model.benchmark_profiles
        : model.metrics
          ? [model.metrics]
          : [];
      return profiles.map((metrics) => ({ model, metrics }));
    });
    return out.sort((a, b) => {
      if (sort === "model") return a.model.name.localeCompare(b.model.name);
      if (sort === "profile") return profileLabel(a.metrics).localeCompare(profileLabel(b.metrics));
      if (sort === "tps") return (b.metrics.throughput_tps ?? -1) - (a.metrics.throughput_tps ?? -1);
      if (sort === "ctx") return (b.metrics.max_ctx_observed ?? -1) - (a.metrics.max_ctx_observed ?? -1);
      if (sort === "tested") return Date.parse(b.metrics.measured_at ?? "0") - Date.parse(a.metrics.measured_at ?? "0");
      return recommendationFor(b.metrics, useCase).score - recommendationFor(a.metrics, useCase).score;
    });
  }, [snapshot.models, sort, useCase]);

  return (
    <section className="cockpit-panel p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold uppercase text-neutral-600 dark:text-neutral-400">
            Model intelligence
          </h2>
          <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">
            Compare measured profiles and retest stale, drifting, or incomplete measurements in place.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2 text-xs text-neutral-600 dark:text-neutral-400">
            Use case
            <select
              value={useCase}
              onChange={(e) => setUseCase(e.target.value as RecommendationUseCase)}
              className="cockpit-input min-h-8 text-xs"
            >
              <option value="chat">Chat</option>
              <option value="code">Code</option>
              <option value="large_context">Large context</option>
              <option value="multi_gpu">Multi-GPU</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-xs text-neutral-600 dark:text-neutral-400">
            Sort
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as BenchmarkSort)}
              className="cockpit-input min-h-8 text-xs"
            >
              <option value="score">Score</option>
              <option value="model">Model</option>
              <option value="profile">Profile</option>
              <option value="tps">Tokens/s</option>
              <option value="ctx">Max context</option>
              <option value="tested">Last tested</option>
            </select>
          </label>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
            <tr>
              <th className="py-2 pr-3 text-left">Model</th>
              <th className="px-3 py-2 text-left">Profile</th>
              <th className="px-3 py-2 text-right">Cold</th>
              <th className="px-3 py-2 text-right">Warm</th>
              <th className="px-3 py-2 text-right">Tokens/s</th>
              <th className="px-3 py-2 text-right">Max ctx</th>
              <th className="px-3 py-2 text-left">VRAM / GPU</th>
              <th className="px-3 py-2 text-left">Trust</th>
              <th className="px-3 py-2 text-left">Trend</th>
              <th className="px-3 py-2 text-left">Score</th>
              <th className="px-3 py-2 text-left">Why</th>
              <th className="py-2 pl-3 text-left">Action</th>
            </tr>
          </thead>
          <tbody>
            {rows.length ? rows.map(({ model, metrics }) => {
              const profile = metrics.benchmark_profile ?? metrics.placement_tested ?? "on_demand";
              const updating = perfRun?.model === model.name && (!perfRun.profiles.length || perfRun.profiles.includes(profile));
              const recommendation = updating
                ? updatingRecommendation(recommendationFor(metrics, useCase))
                : recommendationFor(metrics, useCase);
              return (
              <tr key={`${model.name}-${profile}-${metrics.measured_at ?? "latest"}`} className="border-t border-[var(--cockpit-border)] align-top">
                <td className="py-2 pr-3">
                  <div className="break-all font-medium text-neutral-900 dark:text-neutral-100">{model.name}</div>
                  <div className="text-xs text-neutral-500 dark:text-neutral-400">{model.metadata.parameter_size ?? "size unknown"} {model.metadata.quantization_level ?? ""}</div>
                </td>
                <td className="px-3 py-2">{profileLabel(metrics)}</td>
                <td className="px-3 py-2 text-right font-mono">{fmtSeconds(metrics.cold_load_seconds)}</td>
                <td className="px-3 py-2 text-right font-mono">{fmtSeconds(metrics.warm_load_seconds)}</td>
                <td className="px-3 py-2 text-right font-mono">{metrics.throughput_tps?.toFixed(1) ?? "—"}</td>
                <td className="px-3 py-2 text-right font-mono">{metrics.max_ctx_observed?.toLocaleString() ?? "—"}</td>
                <td className="px-3 py-2 text-xs text-neutral-600 dark:text-neutral-400">{gpuHint(model, metrics)}</td>
                <td className="px-3 py-2"><BenchmarkTrust metrics={metrics} /></td>
                <td className="px-3 py-2"><BenchmarkTrend metrics={metrics} /></td>
                <td className="px-3 py-2"><RecommendationScore recommendation={recommendation} /></td>
                <td className="px-3 py-2">
                  <RecommendationFacts recommendation={recommendation} />
                </td>
                <td className="py-2 pl-3 text-xs text-neutral-600 dark:text-neutral-400">
                  <div className="font-medium text-neutral-700 dark:text-neutral-300">
                    {metrics.measured_at ? new Date(metrics.measured_at).toLocaleString() : "—"}
                  </div>
                  <BenchmarkHistory metrics={metrics} />
                  <button
                    type="button"
                    disabled={Boolean(perfRun)}
                    onClick={() => onRetest(model.name, profile)}
                    className={`mt-2 min-h-7 rounded-md border px-2 py-1 text-xs font-medium ${
                      metrics.retest_recommended
                        ? "border-amber-300 text-amber-800 hover:bg-amber-50 dark:border-amber-800 dark:text-amber-200 dark:hover:bg-amber-950"
                        : "border-[var(--cockpit-border)] text-neutral-700 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-800"
                    } disabled:opacity-50`}
                    title={metrics.retest_reason ?? `Retest ${profileLabel(metrics)}`}
                  >
                    {updating ? "Updating..." : metrics.retest_recommended ? "Retest profile" : "Retest"}
                  </button>
                </td>
              </tr>
            );}) : (
              <tr>
                <td colSpan={12} className="py-8 text-center text-sm text-neutral-500 dark:text-neutral-400">
                  No benchmark profiles yet. Run performance tests from the Live board.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function profileLabel(metrics: { benchmark_profile?: string | null; placement_tested?: string | null }) {
  const profile = metrics.benchmark_profile ?? metrics.placement_tested ?? "on_demand";
  return COLUMN_LABELS[profile] ?? profile.toUpperCase();
}

function fmtSeconds(value: number | null | undefined) {
  return value == null ? "—" : `${value.toFixed(1)}s`;
}

function fmtMb(value: number | null | undefined) {
  if (value == null) return "?";
  if (value >= 1024) return `${(value / 1024).toFixed(1)} GB`;
  return `${Math.round(value)} MB`;
}

function apiErrorSummary(prefix: string, error: ApiError) {
  const detail = error.detail;
  if (detail && typeof detail === "object" && "detail" in detail) {
    const nested = (detail as { detail?: unknown }).detail;
    if (nested && typeof nested === "object") {
      const code = "detail" in nested ? String((nested as { detail?: unknown }).detail ?? "") : "";
      const cause = "cause" in nested ? String((nested as { cause?: unknown }).cause ?? "") : "";
      const parts = [code.replace(/_/g, " "), cause].filter(Boolean);
      if (parts.length) return `${prefix}: ${parts.join(" - ")}`;
    }
    if (typeof nested === "string") return `${prefix}: ${nested.replace(/_/g, " ")}`;
  }
  return `${prefix}: HTTP ${error.status}`;
}

function ageLabel(days: number | null | undefined) {
  if (days == null) return "age unknown";
  if (days < 1) return "today";
  return `${Math.round(days)}d old`;
}

function BenchmarkTrust({ metrics }: { metrics: NonNullable<ModelCardPayload["metrics"]> }) {
  const ageTone =
    metrics.staleness === "old"
      ? "bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-300"
      : metrics.staleness === "stale"
        ? "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
        : "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300";
  const driftTone =
    metrics.drift_status === "warning"
      ? "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
      : metrics.drift_status === "info"
        ? "bg-sky-50 text-sky-700 dark:bg-sky-950 dark:text-sky-300"
        : metrics.drift_status === "stable"
          ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
          : "bg-neutral-100 text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400";
  const driftLabel =
    metrics.drift_status === "warning"
      ? "drift"
      : metrics.drift_status === "info"
        ? "shift"
        : metrics.drift_status;
  return (
    <div className="flex min-w-32 flex-col gap-1 text-xs">
      <div className="flex flex-wrap gap-1">
        <span className={`w-fit rounded-full px-2 py-0.5 font-semibold ${profileStatusTone(metrics.profile_status)}`}>
          {metrics.profile_status}
        </span>
        <span className={`w-fit rounded-full px-2 py-0.5 font-semibold ${ageTone}`}>
          {metrics.staleness === "fresh" ? "fresh" : metrics.staleness}
        </span>
        <span className={`w-fit rounded-full px-2 py-0.5 font-semibold ${driftTone}`}>
          {driftLabel}
        </span>
      </div>
      <div className="text-[11px] text-neutral-500 dark:text-neutral-400">
        {ageLabel(metrics.age_days)}
      </div>
      {metrics.drift_signals?.[0] ? (
        <div className="max-w-40 text-[11px] text-neutral-600 dark:text-neutral-400">
          {metrics.drift_signals[0]}
        </div>
      ) : null}
      {metrics.notes ? (
        <div className="max-w-40 text-[11px] text-amber-700 dark:text-amber-300">
          {metrics.notes}
        </div>
      ) : null}
    </div>
  );
}

function profileStatusTone(status: string) {
  if (status === "success") return "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300";
  if (status === "partial") return "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300";
  if (status === "failed") return "bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-300";
  if (status === "skipped") return "bg-neutral-100 text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400";
  return "bg-neutral-100 text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400";
}

function BenchmarkTrend({ metrics }: { metrics: NonNullable<ModelCardPayload["metrics"]> }) {
  const entries = [
    ["TPS", metrics.trends?.throughput_tps],
    ["Warm", metrics.trends?.warm_load_seconds],
    ["Cold", metrics.trends?.cold_load_seconds],
    ["Ctx", metrics.trends?.max_ctx_observed],
  ] as const;
  const tone =
    metrics.trend_status === "warning"
      ? "text-amber-700 dark:text-amber-300"
      : metrics.trend_status === "info"
        ? "text-sky-700 dark:text-sky-300"
        : metrics.trend_status === "stable"
          ? "text-emerald-700 dark:text-emerald-300"
          : "text-neutral-500 dark:text-neutral-400";
  return (
    <div className="min-w-32 text-xs">
      <div className={`font-semibold ${tone}`}>{metrics.trend_status}</div>
      <div className="mt-1 flex flex-wrap gap-1">
        {entries.map(([label, trend]) => (
          <span
            key={label}
            className="rounded bg-neutral-100 px-1.5 py-0.5 font-mono text-[11px] dark:bg-neutral-900"
            title={trend?.pct_change == null ? "No trend baseline yet" : `${Math.round(trend.pct_change * 100)}% vs recent median`}
          >
            {label} {trendArrow(trend?.direction)}
          </span>
        ))}
      </div>
      {metrics.trend_signals?.[0] ? (
        <div className="mt-1 max-w-40 text-[11px] text-neutral-600 dark:text-neutral-400">
          {metrics.trend_signals[0]}
        </div>
      ) : null}
    </div>
  );
}

function trendArrow(direction: string | undefined) {
  if (direction === "up") return "↑";
  if (direction === "down") return "↓";
  if (direction === "flat") return "→";
  return "?";
}

function BenchmarkHistory({ metrics }: { metrics: NonNullable<ModelCardPayload["metrics"]> }) {
  if (!metrics.history?.length || metrics.history.length < 2) return null;
  return (
    <details className="mt-1 max-w-xs">
      <summary className="cursor-pointer select-none text-[11px] text-neutral-500 hover:text-neutral-800 dark:text-neutral-400 dark:hover:text-neutral-200">
        history ({metrics.history.length})
      </summary>
      <div className="mt-1 overflow-hidden rounded border border-[var(--cockpit-border)]">
        {metrics.history.map((entry, idx) => (
          <div
            key={`${entry.measured_at ?? "run"}-${idx}`}
            className="grid grid-cols-[1fr_auto_auto_auto] gap-2 border-t border-[var(--cockpit-border)] px-2 py-1 first:border-t-0"
          >
            <span>{entry.measured_at ? new Date(entry.measured_at).toLocaleDateString() : "—"}</span>
            <span className="font-mono">{entry.throughput_tps?.toFixed(1) ?? "—"} tps</span>
            <span className="font-mono">{fmtSeconds(entry.cold_load_seconds)}</span>
            <span className="font-mono">{entry.max_ctx_observed?.toLocaleString() ?? "—"}</span>
            {entry.notes ? (
              <span className="col-span-4 text-amber-700 dark:text-amber-300">{entry.notes}</span>
            ) : null}
          </div>
        ))}
      </div>
    </details>
  );
}

function gpuHint(model: ModelCardPayload, metrics: { benchmark_profile?: string | null; gpu_layout_diff?: Record<string, number> }) {
  const layout = Object.entries(metrics.gpu_layout_diff ?? {})
    .filter(([, mb]) => Math.abs(mb) > 0)
    .map(([gpu, mb]) => `${gpu.replace("_vram_growth_mb", "")}: ${mb > 0 ? "+" : ""}${mb} MB`);
  if (layout.length) return layout.join(", ");
  const profile = metrics.benchmark_profile ?? "on_demand";
  if (profile === "on_demand") return model.actual.loaded ? "Currently loaded" : "No pinned GPU";
  if (profile === "multi_gpu") return "Spans visible GPUs; may evict warm single-GPU models";
  return `${profile.toUpperCase()} profile; verify actual placement after loading`;
}

function recommendationFor(metrics: NonNullable<ModelCardPayload["metrics"]>, useCase: RecommendationUseCase) {
  return metrics.recommendations?.find((r) => r.use_case === useCase) ?? {
    use_case: useCase,
    score: 0,
    confidence: "insufficient",
    reasons: ["insufficient measured facts for this recommendation"],
    warnings: ["backend scoring is unavailable for this benchmark row"],
  };
}

function updatingRecommendation(recommendation: ReturnType<typeof recommendationFor>) {
  return {
    ...recommendation,
    confidence: "updating",
    warnings: ["profile retest is running; scores will refresh when the snapshot updates", ...recommendation.warnings],
  };
}

function RecommendationScore({ recommendation }: { recommendation: ReturnType<typeof recommendationFor> }) {
  const tone =
    recommendation.confidence === "high"
      ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
      : recommendation.confidence === "medium"
        ? "bg-sky-50 text-sky-700 dark:bg-sky-950 dark:text-sky-300"
        : recommendation.confidence === "low"
          ? "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
          : "bg-neutral-100 text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400";
  return (
    <div className="flex min-w-24 flex-col gap-1">
      <span className={`w-fit rounded-full px-2 py-0.5 text-xs font-semibold ${tone}`}>
        {recommendation.score}/100
      </span>
      <span className="text-[11px] uppercase text-neutral-500 dark:text-neutral-400">
        {recommendation.confidence}
      </span>
    </div>
  );
}

function RecommendationFacts({ recommendation }: { recommendation: ReturnType<typeof recommendationFor> }) {
  return (
    <details className="max-w-sm text-xs text-neutral-600 dark:text-neutral-400">
      <summary className="cursor-pointer select-none text-neutral-800 dark:text-neutral-200">
        {recommendation.reasons[0] ?? "No reason available"}
      </summary>
      <ul className="mt-1 list-disc space-y-1 pl-4">
        {recommendation.reasons.slice(1).map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
        {recommendation.warnings.map((warning) => (
          <li key={warning} className="text-amber-700 dark:text-amber-300">
            {warning}
          </li>
        ))}
      </ul>
    </details>
  );
}

function ColumnView(props: {
  col: string;
  models: ModelCardPayload[];
  gpus: GpuPayload[];
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
  const { col, models, gpus, isAdmin, busyModel } = props;
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
              gpus={gpus}
              isAdmin={isAdmin}
              busy={busyModel === m.name}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function ModelCardView({
  m,
  gpus,
  isAdmin,
  busy,
}: {
  m: ModelCardPayload;
  gpus: GpuPayload[];
  isAdmin: boolean;
  busy: boolean;
}) {
  const placement = m.config?.placement ?? "on_demand";
  const metadataBits = [m.metadata?.parameter_size, m.metadata?.quantization_level].filter(Boolean);
  const requestedWarm = isWarmColumn(placement);
  const singleMetrics = preferredSingleProfile(m);
  const tensorMetrics = preferredTensorProfile(m);
  const singleCtx = singleMetrics?.max_ctx_observed ?? m.context?.max_measured_ctx ?? m.context?.max_estimated_ctx ?? null;
  const tensorCtx = tensorMetrics?.max_ctx_observed ?? null;
  const heat = heatStatusFor(m, gpus);
  const actualLabel =
    m.actual.gpu_layout && Object.keys(m.actual.gpu_layout).length
      ? Object.entries(m.actual.gpu_layout)
          .map(([gpu, mb]) => `${gpu}:${Math.round(mb / 1024)}GB`)
          .join(" ")
      : m.actual.loaded && m.actual.vram_mb != null
        ? `in VRAM ${fmtMb(m.actual.vram_mb)}`
        : m.actual.main_gpu_actual != null
        ? `GPU ${m.actual.main_gpu_actual}`
        : m.actual.loaded
          ? "in VRAM"
          : requestedWarm
            ? "not in VRAM"
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
        </div>
      </div>
      <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-neutral-600 dark:text-neutral-400">
        <span>{actualLabel}</span>
        <span className={`rounded-full px-2 py-0.5 font-semibold ${heat.cls}`} title={heat.title}>
          {heat.label}
        </span>
      </div>
      {m.actual.mismatch ? (
        <div className="text-xs text-rose-600 mt-0.5">
          Requested {placement} · Ollama placed on GPU {m.actual.main_gpu_actual}
        </div>
      ) : null}
      <div className="mt-2 grid grid-cols-2 gap-1 text-[11px]">
        <CardMetricTile label="calls 30d" value={m.calls_30d.toLocaleString()} />
        <CardMetricTile label="cold" value={singleMetrics?.cold_load_seconds != null ? `${singleMetrics.cold_load_seconds.toFixed(1)}s` : "—"} />
        <CardMetricTile label="tks/s single" value={singleMetrics?.throughput_tps != null ? singleMetrics.throughput_tps.toFixed(1) : "—"} />
        <CardMetricTile label="tks/s tensor" value={tensorMetrics?.throughput_tps != null ? tensorMetrics.throughput_tps.toFixed(1) : "—"} />
        <CardMetricTile label="ctx single" value={singleCtx?.toLocaleString() ?? "—"} />
        <CardMetricTile label="ctx tensor" value={tensorCtx?.toLocaleString() ?? "—"} />
      </div>
      {!m.metrics && !m.benchmark_profiles.length ? (
        <div className="text-xs text-neutral-500 italic mt-1">
          no perf data
        </div>
      ) : null}
    </li>
  );
}

function CardMetricTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded bg-neutral-100 px-2 py-1 dark:bg-neutral-900">
      <div className="text-[10px] uppercase text-neutral-500 dark:text-neutral-400">{label}</div>
      <div className="font-mono text-xs text-neutral-900 dark:text-neutral-100">{value}</div>
    </div>
  );
}

function preferredSingleProfile(m: ModelCardPayload) {
  return (
    m.benchmark_profiles.find((p) => p.benchmark_profile?.startsWith("gpu")) ??
    m.benchmark_profiles.find((p) => p.benchmark_profile === "on_demand") ??
    m.metrics
  );
}

function preferredTensorProfile(m: ModelCardPayload) {
  return m.benchmark_profiles.find((p) => p.benchmark_profile === "multi_gpu") ?? null;
}

function heatStatusFor(m: ModelCardPayload, gpus: GpuPayload[]) {
  const temp = modelHeatTemp(m, gpus);
  if (temp == null) {
    return {
      label: "heat",
      title: "No GPU temperature telemetry available",
      cls: "bg-neutral-100 text-neutral-500 dark:bg-neutral-900 dark:text-neutral-400",
    };
  }
  if (temp >= 89) {
    return {
      label: "heat",
      title: `Huge thermal throttling risk (${Math.round(temp)} C)`,
      cls: "bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300",
    };
  }
  if (temp >= 82) {
    return {
      label: "heat",
      title: `Some thermal throttling risk (${Math.round(temp)} C)`,
      cls: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
    };
  }
  return {
    label: "heat",
    title: `No thermal throttling signal (${Math.round(temp)} C)`,
    cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  };
}

function modelHeatTemp(m: ModelCardPayload, gpus: GpuPayload[]): number | null {
  const byIndex = new Map(gpus.map((g) => [g.index, g.temp_c]));
  const temps: number[] = [];
  if (m.actual.gpu_layout) {
    for (const key of Object.keys(m.actual.gpu_layout)) {
      const match = key.match(/\d+/);
      if (!match) continue;
      const temp = byIndex.get(Number(match[0]));
      if (temp != null) temps.push(temp);
    }
  }
  if (!temps.length && m.actual.main_gpu_actual != null) {
    const temp = byIndex.get(m.actual.main_gpu_actual);
    if (temp != null) temps.push(temp);
  }
  if (!temps.length && /^gpu\d+$/.test(m.config.placement)) {
    const temp = byIndex.get(Number(m.config.placement.slice(3)));
    if (temp != null) temps.push(temp);
  }
  if (!temps.length && m.config.placement === "multi_gpu") {
    temps.push(...gpus.map((g) => g.temp_c).filter((temp): temp is number => temp != null));
  }
  return temps.length ? Math.max(...temps) : null;
}

const PERF_STAGE_LABELS: Record<string, string> = {
  starting: "Starting",
  lock: "Waiting for lock",
  unload: "Unload",
  cold_load: "Cold load",
  warm_load: "Warm load",
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
            {run.profiles.length ? (
              <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
                Retesting {run.profiles.map((profile) => COLUMN_LABELS[profile] ?? profile.toUpperCase()).join(", ")}
              </p>
            ) : null}
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
          {running || run.currentProfile ? (
            <div className="rounded-md border border-[var(--cockpit-border)] bg-[var(--cockpit-surface-muted)] px-3 py-2 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-neutral-600 dark:text-neutral-400">Current profile</span>
                <span className="font-mono text-neutral-900 dark:text-neutral-100">
                  {run.currentProfile ? (COLUMN_LABELS[run.currentProfile] ?? run.currentProfile.toUpperCase()) : "All profiles"}
                </span>
              </div>
              {run.profiles.length ? (
                <div className="mt-2 flex flex-wrap gap-1">
                  {run.profiles.map((profile) => (
                    <span
                      key={profile}
                      className={`rounded px-1.5 py-0.5 text-[11px] ${
                        profile === run.currentProfile
                          ? "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200"
                          : "bg-neutral-100 text-neutral-600 dark:bg-neutral-900 dark:text-neutral-400"
                      }`}
                    >
                      {COLUMN_LABELS[profile] ?? profile.toUpperCase()}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}

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
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <MetricTile
                  label="Cold load"
                  value={
                    run.result.cold_load_seconds == null
                      ? "—"
                      : `${run.result.cold_load_seconds.toFixed(1)} s`
                  }
                />
                <MetricTile
                  label="Warm load"
                  value={
                    run.result.warm_load_seconds == null
                      ? "—"
                      : `${run.result.warm_load_seconds.toFixed(1)} s`
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
              {run.result.profiles?.length ? (
                <div>
                  <div className="text-xs font-semibold uppercase text-neutral-500 mb-1">
                    Profiles tested
                  </div>
                  <div className="space-y-1">
                    {run.result.profiles.map((profile, idx) => (
                      <div key={idx} className="grid grid-cols-5 gap-2 rounded bg-white/70 px-2 py-1 text-xs dark:bg-neutral-950/70">
                        <span>{profileLabel(profile)}</span>
                        <span className="font-mono">{fmtSeconds(profile.cold_load_seconds)}</span>
                        <span className="font-mono">{profile.throughput_tps?.toFixed(1) ?? "—"} tps</span>
                        <span className="font-mono">{profile.max_ctx_observed?.toLocaleString() ?? "—"} ctx</span>
                        <span className={profile.notes ? "text-amber-700 dark:text-amber-300" : "text-neutral-500"}>
                          {profile.notes ? "partial" : "ok"}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
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
