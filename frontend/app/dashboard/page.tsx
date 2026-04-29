"use client";

import { useEffect, useState } from "react";

import { AppHeader } from "@/components/AppHeader";
import { DashboardHistory } from "@/components/DashboardHistory";
import { ApiError, api, streamSse } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import {
  COLUMN_LABELS,
  DashboardSnapshot,
  ModelCardPayload,
  WARM_COLUMNS,
  fmtBytes,
} from "@/lib/dashboard-types";

type DashboardTab = "live" | "history";
type PerfStatus = "idle" | "running" | "result" | "cancelled" | "error";

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

  async function onPlacementChange(model: string, placement: string) {
    setBusyModel(model);
    try {
      await api(`/api/admin/ollama/models/${encodeURIComponent(model)}/place`, {
        method: "POST",
        body: JSON.stringify({ placement }),
      });
    } catch (e) {
      if (e instanceof ApiError) {
        alert(`Place failed: ${e.status}\n${JSON.stringify(e.detail)}`);
      }
    } finally {
      setBusyModel(null);
    }
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

  async function onCancelPerfTest(model: string) {
    setPerfRun((prev) => (prev ? { ...prev, cancelling: true } : prev));
    try {
      await api(`/api/admin/ollama/models/${encodeURIComponent(model)}/perf-test/cancel`, {
        method: "POST",
      });
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
  for (const m of snapshot.models) {
    const placement = m.config?.placement ?? "available";
    if (buckets.has(placement)) {
      buckets.get(placement)!.push(m);
    } else {
      buckets.get("available")?.push(m);
    }
  }

  return (
    <>
      <AppHeader />
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 py-4 space-y-4">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setTab("live")}
            className={`px-3 py-1.5 rounded-md text-sm font-medium border ${
              tab === "live"
                ? "bg-neutral-900 text-white border-neutral-900 dark:bg-neutral-100 dark:text-neutral-900 dark:border-neutral-100"
                : "bg-white dark:bg-neutral-900 text-neutral-700 dark:text-neutral-300 border-neutral-200 dark:border-neutral-800 hover:bg-neutral-50 dark:hover:bg-neutral-800"
            }`}
          >
            Live
          </button>
          <button
            onClick={() => setTab("history")}
            className={`px-3 py-1.5 rounded-md text-sm font-medium border ${
              tab === "history"
                ? "bg-neutral-900 text-white border-neutral-900 dark:bg-neutral-100 dark:text-neutral-900 dark:border-neutral-100"
                : "bg-white dark:bg-neutral-900 text-neutral-700 dark:text-neutral-300 border-neutral-200 dark:border-neutral-800 hover:bg-neutral-50 dark:hover:bg-neutral-800"
            }`}
          >
            History
          </button>
        </div>

        {tab === "live" ? (
          <>
            <div className="flex items-center gap-3 flex-wrap">
              <StatusPill status={snapshot.status} />
              <div className="flex flex-wrap gap-2">
                {snapshot.gpus.length === 0 ? (
                  <span className="text-sm text-neutral-500">
                    No GPU telemetry detected (Mac / CPU-only / nvidia-smi missing).
                  </span>
                ) : (
                  snapshot.gpus.map((g) => (
                    <GpuStripItem key={g.index} g={g} />
                  ))
                )}
              </div>
            </div>

            <section className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
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
    <div className="rounded-md border border-neutral-200 dark:border-neutral-800 px-3 py-2 text-sm bg-white dark:bg-neutral-900 min-w-[220px]">
      <div className="font-semibold">GPU {g.index}</div>
      <div className="text-xs text-neutral-600 dark:text-neutral-400 mt-0.5">
        {usedGb.toFixed(1)} / {totalGb.toFixed(1)} GB VRAM
      </div>
      {g.temp_c != null && tempStatus ? (
        <div className="mt-1 flex items-center gap-2 text-xs">
          <span className="font-mono text-neutral-700 dark:text-neutral-300">
            {Math.round(g.temp_c)}°C
          </span>
          <span
            className={`px-1.5 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wide ${tempStatus.cls}`}
            title="RTX 3090 thresholds: ≤70 Good · 71–82 Workload · 83–89 Throttling · ≥90 Critical"
          >
            {tempStatus.label}
          </span>
        </div>
      ) : null}
      {g.power_w != null ? (
        <div className={`text-xs font-mono mt-1 ${wattsCls}`}>
          {g.power_w.toFixed(0)} W / {g.max_power_w ?? DEFAULT_TDP_W} W
        </div>
      ) : null}
    </div>
  );
}

function ColumnView(props: {
  col: string;
  models: ModelCardPayload[];
  columns: string[];
  isAdmin: boolean;
  busyModel: string | null;
  onPlacementChange: (model: string, placement: string) => void;
  onDelete: (model: string) => void;
  onPerfTest: (model: string) => void;
}) {
  const { col, models, columns, isAdmin, busyModel } = props;
  const label = COLUMN_LABELS[col] ?? col.toUpperCase();
  const warm = WARM_COLUMNS.has(col);
  return (
    <section
      className={`rounded-md border p-2 ${
        warm
          ? "border-emerald-300 dark:border-emerald-800 bg-emerald-50/40 dark:bg-emerald-950/20"
          : "border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900"
      }`}
    >
      <h2 className="text-xs font-semibold uppercase tracking-wide text-neutral-600 dark:text-neutral-400 mb-2">
        {label}
      </h2>
      {models.length === 0 ? (
        <p className="text-xs text-neutral-400 italic">empty</p>
      ) : (
        <ul className="space-y-2">
          {models.map((m) => (
            <ModelCardView
              key={m.name}
              m={m}
              columns={columns}
              isAdmin={isAdmin}
              busy={busyModel === m.name}
              onPlacementChange={(placement) => props.onPlacementChange(m.name, placement)}
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
  onPlacementChange: (placement: string) => void;
  onDelete: () => void;
  onPerfTest: () => void;
}) {
  const placement = m.config?.placement ?? "available";
  return (
    <li className="rounded border border-neutral-200 dark:border-neutral-800 p-2 text-sm bg-white dark:bg-neutral-950">
      <div className="font-semibold break-all">{m.name}</div>
      <div className="text-xs text-neutral-600 dark:text-neutral-400 mt-0.5">
        tag: {m.tag ?? "—"} · {fmtBytes(m.size_bytes)} ·{" "}
        {m.actual.loaded ? "loaded" : "idle"}
      </div>
      {/* Sprint 5b — show the configured context window so admins see the
          VRAM budget at a glance. Falls back to "—" when no model_config
          row exists or num_ctx_default is null. */}
      <div className="text-xs text-neutral-500 dark:text-neutral-400 mt-0.5 font-mono">
        ctx {m.config?.num_ctx_default?.toLocaleString() ?? "—"}
      </div>
      {m.actual.mismatch ? (
        <div className="text-xs text-rose-600 mt-0.5">
          Requested {placement} · Ollama placed on GPU {m.actual.main_gpu_actual}
        </div>
      ) : null}
      {m.metrics ? (
        <div className="text-xs font-mono text-neutral-600 dark:text-neutral-400 mt-1">
          ⏱ cold {m.metrics.cold_load_seconds?.toFixed(1) ?? "—"} s · ⚡{" "}
          {m.metrics.throughput_tps?.toFixed(1) ?? "—"} tps ·{" "}
          {m.metrics.max_ctx_observed ?? "?"} ctx
        </div>
      ) : (
        <div className="text-xs text-neutral-500 italic mt-1">
          no perf data — run Test performance
        </div>
      )}
      {isAdmin ? (
        <div className="flex flex-wrap gap-1 mt-2">
          <select
            className="text-xs rounded border border-neutral-300 dark:border-neutral-700 bg-transparent px-1 py-0.5"
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
          <button
            type="button"
            disabled={busy}
            onClick={onPerfTest}
            className="text-xs rounded border border-neutral-300 dark:border-neutral-700 px-2 py-0.5 hover:bg-neutral-100 dark:hover:bg-neutral-800"
          >
            Test performance
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onDelete}
            className="text-xs rounded border border-rose-300 dark:border-rose-800 text-rose-700 dark:text-rose-300 px-2 py-0.5 hover:bg-rose-50 dark:hover:bg-rose-950"
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
    <div className="fixed inset-0 z-50 bg-black/40 flex justify-end">
      <aside className="h-full w-full max-w-xl bg-white dark:bg-neutral-950 shadow-2xl border-l border-neutral-200 dark:border-neutral-800 p-5 overflow-y-auto">
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
            className="text-sm text-neutral-600 hover:text-neutral-900 dark:hover:text-neutral-100"
          >
            Close
          </button>
        </div>

        <div className="mt-5 rounded-md border border-neutral-200 dark:border-neutral-800 p-4 space-y-4">
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
                className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm font-medium hover:bg-neutral-100 dark:hover:bg-neutral-800"
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
    <div className="rounded-md border border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-900 p-2">
      <div className="text-[11px] uppercase font-semibold text-neutral-500">{label}</div>
      <div className="font-mono text-sm mt-1">{value}</div>
    </div>
  );
}
