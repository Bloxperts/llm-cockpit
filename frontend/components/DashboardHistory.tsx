"use client";

// UC-03 — Dashboard History tab.
//
// Four cards rendered from /api/dashboard/history. Each card lazy-fetches
// its own metric on mount; switching between 24 h and 7 d sub-tabs
// remounts and re-fetches. We intentionally don't share a single big
// query because the four metrics return different series shapes and
// caching them independently keeps the loading state per-card honest.
//
// Recharts is the chart library (added in package.json under recharts
// ^3.x). Imported only by this component so the dashboard's Live tab
// doesn't pay the bundle cost.

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ApiError, api } from "@/lib/api";

type RangeT = "24h" | "7d";
type MetricT = "gpu_temp" | "vram" | "calls" | "latency";

interface HistoryPoint {
  ts: string;
  value: number | null;
}

interface HistorySeries {
  label: string;
  data: HistoryPoint[];
}

interface HistoryResponse {
  series: HistorySeries[];
}

// Per-GPU colour palette mirrors the GPU temp status thresholds elsewhere
// in the dashboard so the user gets a consistent visual identity for each
// GPU index across Live and History views.
const GPU_COLOURS = [
  "#10b981", // emerald — GPU 0
  "#0ea5e9", // sky — GPU 1
  "#f59e0b", // amber — GPU 2
  "#f43f5e", // rose — GPU 3
];

const LATENCY_COLOURS: Record<string, string> = {
  p50: "#0ea5e9",
  p95: "#f43f5e",
};

function fmtTimestamp(ts: string, range: RangeT): string {
  // Server returns naive UTC ISO (e.g. "2026-04-28T12:34:00"). Parse as UTC
  // and format in the user's local zone.
  const dt = new Date(ts.endsWith("Z") ? ts : ts + "Z");
  if (Number.isNaN(dt.getTime())) return ts;
  if (range === "24h") {
    return dt.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  return dt.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function useHistory(metric: MetricT, range: RangeT) {
  return useQuery<HistoryResponse>({
    queryKey: ["history", metric, range],
    queryFn: () =>
      api<HistoryResponse>(
        `/api/dashboard/history?range=${range}&metric=${metric}`,
      ),
    staleTime: 60_000,
  });
}

interface ChartData {
  ts: string;
  tsLabel: string;
  [series: string]: string | number | null;
}

function pivot(
  series: HistorySeries[],
  range: RangeT,
): { rows: ChartData[]; labels: string[] } {
  // Recharts wants one row per timestamp with one key per series. The
  // server returns one array per series; pivot here.
  const byTs = new Map<string, ChartData>();
  for (const s of series) {
    for (const pt of s.data) {
      const row = byTs.get(pt.ts) ?? {
        ts: pt.ts,
        tsLabel: fmtTimestamp(pt.ts, range),
      };
      row[s.label] = pt.value;
      byTs.set(pt.ts, row);
    }
  }
  const rows = Array.from(byTs.values()).sort((a, b) => a.ts.localeCompare(b.ts));
  return { rows, labels: series.map((s) => s.label) };
}

// --- Cards --------------------------------------------------------------

function Card({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="cockpit-panel flex flex-col p-4">
      <div className="flex items-baseline justify-between mb-2">
        <h3 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
          {title}
        </h3>
        {subtitle ? (
          <span className="text-xs text-neutral-500 dark:text-neutral-400">
            {subtitle}
          </span>
        ) : null}
      </div>
      <div className="flex-1 min-h-[240px]">{children}</div>
    </div>
  );
}

function Skeleton() {
  return (
    <div className="h-full w-full animate-pulse bg-neutral-100 dark:bg-neutral-800 rounded" />
  );
}

function EmptyState({ message }: { message?: string }) {
  return (
    <div className="h-full w-full flex items-center justify-center text-sm text-neutral-500 dark:text-neutral-400">
      {message ?? "No data yet — GPU metrics accumulate over time."}
    </div>
  );
}

function ChartFrame({
  query,
  range,
  emptyMessage,
  render,
}: {
  query: ReturnType<typeof useHistory>;
  range: RangeT;
  emptyMessage?: string;
  render: (rows: ChartData[], labels: string[]) => React.ReactNode;
}) {
  if (query.isLoading) return <Skeleton />;
  if (query.isError) {
    const e = query.error;
    return (
      <EmptyState
        message={
          e instanceof ApiError ? `Failed to load (${e.status})` : "Failed to load"
        }
      />
    );
  }
  const series = query.data?.series ?? [];
  if (series.length === 0 || series.every((s) => s.data.length === 0)) {
    return <EmptyState message={emptyMessage} />;
  }
  const { rows, labels } = pivot(series, range);
  return <ResponsiveContainer width="100%" height="100%">{render(rows, labels) as React.ReactElement}</ResponsiveContainer>;
}

function GpuTempCard({ range }: { range: RangeT }) {
  const q = useHistory("gpu_temp", range);
  return (
    <Card title="GPU Temperature" subtitle="°C">
      <ChartFrame
        query={q}
        range={range}
        render={(rows, labels) => (
          <LineChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-neutral-200 dark:stroke-neutral-800" />
            <XAxis
              dataKey="tsLabel"
              tick={{ fontSize: 11 }}
              minTickGap={32}
            />
            <YAxis tick={{ fontSize: 11 }} domain={[0, 100]} unit="°" />
            <Tooltip />
            {labels.map((label, idx) => (
              <Line
                key={label}
                type="monotone"
                dataKey={label}
                stroke={GPU_COLOURS[idx % GPU_COLOURS.length]}
                strokeWidth={2}
                dot={false}
                connectNulls
                name={label}
              />
            ))}
          </LineChart>
        )}
      />
    </Card>
  );
}

function VramCard({ range }: { range: RangeT }) {
  const q = useHistory("vram", range);
  return (
    <Card title="VRAM Used" subtitle="MB">
      <ChartFrame
        query={q}
        range={range}
        render={(rows, labels) => (
          <AreaChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-neutral-200 dark:stroke-neutral-800" />
            <XAxis
              dataKey="tsLabel"
              tick={{ fontSize: 11 }}
              minTickGap={32}
            />
            <YAxis tick={{ fontSize: 11 }} unit=" MB" />
            <Tooltip />
            {labels.map((label, idx) => (
              <Area
                key={label}
                type="monotone"
                dataKey={label}
                stackId="vram"
                stroke={GPU_COLOURS[idx % GPU_COLOURS.length]}
                fill={GPU_COLOURS[idx % GPU_COLOURS.length]}
                fillOpacity={0.35}
                name={label}
              />
            ))}
          </AreaChart>
        )}
      />
    </Card>
  );
}

function CallsCard({ range }: { range: RangeT }) {
  const q = useHistory("calls", range);
  const subtitle = range === "24h" ? "calls / minute" : "calls / hour";
  return (
    <Card title="Request Rate" subtitle={subtitle}>
      <ChartFrame
        query={q}
        range={range}
        emptyMessage="No assistant calls in this window."
        render={(rows, labels) => (
          <BarChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-neutral-200 dark:stroke-neutral-800" />
            <XAxis
              dataKey="tsLabel"
              tick={{ fontSize: 11 }}
              minTickGap={32}
            />
            <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
            <Tooltip />
            {labels.map((label) => (
              <Bar
                key={label}
                dataKey={label}
                fill="#0ea5e9"
                name={label}
              />
            ))}
          </BarChart>
        )}
      />
    </Card>
  );
}

function LatencyCard({ range }: { range: RangeT }) {
  const q = useHistory("latency", range);
  return (
    <Card title="Latency" subtitle="ms (p50 / p95)">
      <ChartFrame
        query={q}
        range={range}
        emptyMessage="No latency samples in this window."
        render={(rows, labels) => (
          <LineChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-neutral-200 dark:stroke-neutral-800" />
            <XAxis
              dataKey="tsLabel"
              tick={{ fontSize: 11 }}
              minTickGap={32}
            />
            <YAxis tick={{ fontSize: 11 }} unit=" ms" />
            <Tooltip />
            {labels.map((label) => (
              <Line
                key={label}
                type="monotone"
                dataKey={label}
                stroke={LATENCY_COLOURS[label] ?? "#94a3b8"}
                strokeWidth={2}
                dot={false}
                connectNulls
                name={label}
              />
            ))}
          </LineChart>
        )}
      />
    </Card>
  );
}

// --- Top-level history view --------------------------------------------

export function DashboardHistory() {
  const [range, setRange] = useState<RangeT>("24h");

  const subTabs = useMemo(
    () => [
      { id: "24h" as const, label: "24 h" },
      { id: "7d" as const, label: "7 d" },
    ],
    [],
  );

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        {subTabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setRange(tab.id)}
            className={`px-3 py-1 rounded-md text-sm border ${
              range === tab.id
                ? "bg-neutral-900 text-white border-neutral-900 dark:bg-neutral-100 dark:text-neutral-900 dark:border-neutral-100"
                : "bg-white dark:bg-neutral-900 text-neutral-700 dark:text-neutral-300 border-neutral-200 dark:border-neutral-800 hover:bg-neutral-50 dark:hover:bg-neutral-800"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <GpuTempCard range={range} />
        <VramCard range={range} />
        <CallsCard range={range} />
        <LatencyCard range={range} />
      </div>
    </div>
  );
}
