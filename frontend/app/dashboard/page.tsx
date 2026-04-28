"use client";

import { useEffect, useRef, useState } from "react";

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
  const [perfModel, setPerfModel] = useState<string | null>(null);
  const [perfLog, setPerfLog] = useState<string[]>([]);
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
    setPerfModel(model);
    setPerfLog([]);
    try {
      for await (const ev of streamSse(
        `/api/admin/ollama/models/${encodeURIComponent(model)}/perf-test`,
        { method: "POST", body: JSON.stringify({}) },
      )) {
        setPerfLog((prev) => [...prev, `[${ev.event}] ${ev.data}`]);
      }
    } catch (e) {
      if (e instanceof ApiError) {
        setPerfLog((prev) => [...prev, `error HTTP ${e.status}`]);
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

      {perfModel ? (
        <PerfDialog
          model={perfModel}
          log={perfLog}
          onClose={() => {
            setPerfModel(null);
            setPerfLog([]);
          }}
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
          no perf data — run "Test performance"
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

function PerfDialog({
  model,
  log,
  onClose,
}: {
  model: string;
  log: string[];
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [log]);
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50">
      <div className="rounded-lg bg-white dark:bg-neutral-900 max-w-2xl w-full p-4 shadow-lg space-y-3">
        <div className="flex items-baseline justify-between">
          <h2 className="text-lg font-semibold">Performance test</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-sm text-neutral-600 hover:text-neutral-900 dark:hover:text-neutral-100"
          >
            Close
          </button>
        </div>
        <p className="text-sm text-neutral-600 dark:text-neutral-400">Model: {model}</p>
        <div
          ref={ref}
          className="font-mono text-xs h-64 overflow-y-auto rounded bg-neutral-100 dark:bg-neutral-950 p-2 whitespace-pre-wrap"
        >
          {log.length === 0 ? <em className="text-neutral-500">Connecting…</em> : log.join("\n")}
        </div>
      </div>
    </div>
  );
}
