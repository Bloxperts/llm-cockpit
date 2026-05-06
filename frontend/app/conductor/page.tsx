"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AppHeader } from "@/components/AppHeader";
import {
  type ConductorContextReport,
  type ConductorManifestDetail,
  type ConductorOverview,
  getConductorContextReport,
  getConductorManifestDetail,
  getConductorOverview,
} from "@/lib/api";

type Metric = {
  label: string;
  value: string | number;
  detail?: string;
};

type CompressionRow = {
  zone: string;
  before: unknown;
  after: unknown;
  ratio: unknown;
};

type RetrievalQuality = NonNullable<ConductorContextReport["quality"]>;

export default function ConductorPage() {
  const [overview, setOverview] = useState<ConductorOverview>(emptyOverview("loading"));
  const [contextReport, setContextReport] = useState<ConductorContextReport>(
    emptyContextReport("loading"),
  );
  const [selectedManifestId, setSelectedManifestId] = useState<string | null>(null);
  const [manifestDetail, setManifestDetail] = useState<ConductorManifestDetail | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const [overviewResult, reportResult] = await Promise.allSettled([
        getConductorOverview(),
        getConductorContextReport(),
      ]);
      if (cancelled) return;
      setOverview(
        overviewResult.status === "fulfilled"
          ? overviewResult.value
          : emptyOverview(overviewResult.reason?.message ?? "overview_failed"),
      );
      setContextReport(
        reportResult.status === "fulfilled"
          ? reportResult.value
          : emptyContextReport(reportResult.reason?.message ?? "context_report_failed"),
      );
    }

    load();
    const interval = window.setInterval(load, 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  const stats = overview.overview;
  const latest = overview.latest_manifest ?? null;
  const recentManifests = overview.recent_manifests ?? [];
  const activeManifestId = selectedManifestId ?? stringValue(latest?.id) ?? null;
  const visibleManifestDetail = activeManifestId ? manifestDetail : null;
  const report = contextReport.report ?? {};
  const quality = contextReport.quality ?? summarizeRetrieval(report);
  const contextSummary = summarizeContext(report);
  const metrics: Metric[] = [
    { label: "Calls", value: stats?.call_count ?? "—", detail: "shadow manifests" },
    { label: "Failures", value: stats?.failure_count ?? "—", detail: "classified outcomes" },
    { label: "Fallbacks", value: stats?.fallback_count ?? "—", detail: "routing changes" },
    { label: "Cache hits", value: stats?.cache_hit_count ?? "—", detail: pct(stats?.cache_hit_rate) },
    { label: "Tokens in", value: stats?.total_tokens_in ?? "—", detail: "realised prompt total" },
    { label: "Tokens out", value: stats?.total_tokens_out ?? "—", detail: "adapter response" },
    { label: "Cost", value: usd(stats?.total_cost_usd), detail: "shadow staging" },
    {
      label: "Manifest coverage",
      value: pctNumber(stats?.manifest_coverage_percent),
      detail: `${overview.manifest_count ?? 0} records`,
    },
  ];
  const manifestRows: Array<[string, unknown]> = [
    ["Adapter", latest?.adapter],
    ["Node", latest?.node],
    ["Capability", latest?.capability],
    ["Tier", latest?.tier],
    ["Runtime", latest?.runtime_mode],
    ["Retrieval", latest?.retrieval_mode],
    ["Context limit", pick(latest ?? {}, ["routing.context_window_limit"])],
    ["Input limit", pick(latest ?? {}, ["routing.input_budget_limit"])],
    ["Tokens in", latest?.tokens_in_total],
    ["Tokens out", latest?.tokens_out],
    ["Cost", usdNumber(latest?.cost_usd)],
  ];

  useEffect(() => {
    if (!activeManifestId) {
      return;
    }
    let cancelled = false;
    getConductorManifestDetail(activeManifestId)
      .then((detail) => {
        if (!cancelled) setManifestDetail(detail);
      })
      .catch((error: Error) => {
        if (!cancelled) {
          setManifestDetail({
            reachable: false,
            surface: "blox-cockpit.conductor",
            updated_at: new Date().toISOString(),
            error: error.message,
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [activeManifestId]);

  return (
    <>
      <AppHeader />
      <main className="mx-auto w-full max-w-7xl flex-1 p-6">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <Link href="/dashboard/" className="mb-2 inline-block text-sm text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100">
            Dashboard
          </Link>
          <h1 className="text-2xl font-semibold">Conductor</h1>
          <p className="mt-1 text-sm text-slate-500">
            Cortex shadow telemetry, context visibility, and manifest coverage.
          </p>
        </div>
        <Status reachable={overview.reachable && contextReport.reachable} />
      </header>

      {(!overview.reachable || !contextReport.reachable) && (
        <section className="mb-5 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100">
          Cortex read is degraded. Overview: {overview.error ?? "ok"}. Context report:{" "}
          {contextReport.error ?? "ok"}.
        </section>
      )}

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {metrics.map((metric) => (
          <div
            key={metric.label}
            className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900"
          >
            <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
              {metric.label}
            </div>
            <div className="mt-2 text-2xl font-semibold">{metric.value}</div>
            {metric.detail && <div className="mt-1 text-xs text-slate-500">{metric.detail}</div>}
          </div>
        ))}
      </section>

      <section className="mt-6 grid gap-5 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
          <SectionHeader title="Context Build" subtitle="Token totals, compression, and graph retrieval quality" />
          <div className="grid gap-4 border-t border-slate-200 p-4 dark:border-slate-800 md:grid-cols-4">
            <ContextMetric
              label="Compressible raw"
              value={contextSummary.compressibleBefore}
              detail="retrieval + tool output"
            />
            <ContextMetric
              label="Compressible after"
              value={contextSummary.compressibleAfter}
              detail={contextSummary.compressibleSaved}
            />
            <ContextMetric
              label="Full prompt before"
              value={contextSummary.fullBefore}
              detail="after standard drop"
            />
            <ContextMetric
              label="Full prompt after"
              value={contextSummary.fullAfter}
              detail={contextSummary.fullSaved}
            />
          </div>
          <div className="border-t border-slate-200 px-4 py-3 text-sm text-slate-600 dark:border-slate-800 dark:text-slate-400">
            Fixed prompt overhead after compression:{" "}
            <span className="font-medium text-slate-900 dark:text-slate-100">
              {display(contextSummary.fixedOverheadAfter)}
            </span>{" "}
            tokens. This separates context compression from mandatory request, summary, and prompt
            structure.
          </div>
          <CompressionTable report={report} />
          <ContextZonesTable report={report} />
          <ContextQualityPanel quality={quality} />
        </div>

        <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
          <SectionHeader title="Latest Manifest" subtitle={String(latest?.id ?? "No manifest yet")} />
          <dl className="divide-y divide-slate-200 border-t border-slate-200 text-sm dark:divide-slate-800 dark:border-slate-800">
            {manifestRows.map(([label, value]) => (
              <div key={label} className="grid grid-cols-[8rem_1fr] gap-3 px-4 py-2">
                <dt className="text-slate-500">{label}</dt>
                <dd className="min-w-0 truncate font-medium">{display(value)}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <section className="mt-6 grid gap-5 lg:grid-cols-[0.8fr_1.2fr]">
        <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
          <SectionHeader title="Manifest Drilldown" subtitle="Recent ADR-012 records" />
          <div className="divide-y divide-slate-200 border-t border-slate-200 dark:divide-slate-800 dark:border-slate-800">
            {recentManifests.length ? (
              recentManifests.map((item) => {
                const id = stringValue(item.id);
                const active = id === activeManifestId;
                return (
                  <button
                    key={id ?? JSON.stringify(item)}
                    type="button"
                    onClick={() => id && setSelectedManifestId(id)}
                    className={[
                      "block w-full px-4 py-3 text-left text-sm",
                      active
                        ? "bg-slate-100 dark:bg-slate-800"
                        : "hover:bg-slate-50 dark:hover:bg-slate-800/70",
                    ].join(" ")}
                  >
                    <span className="block truncate font-medium">{display(id)}</span>
                    <span className="mt-1 block text-xs text-slate-500">
                      {display(item.capability)} · {display(item.tier)} · {display(item.status)}
                    </span>
                  </button>
                );
              })
            ) : (
              <p className="p-4 text-sm text-slate-500">No manifest rows yet.</p>
            )}
          </div>
        </div>

        <div className="rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
          <SectionHeader
            title="Full Record"
            subtitle={visibleManifestDetail?.reachable ? display(activeManifestId) : visibleManifestDetail?.error ?? "Select a manifest"}
          />
          <pre className="max-h-[34rem] overflow-auto border-t border-slate-200 p-4 text-xs leading-5 dark:border-slate-800">
            {visibleManifestDetail?.manifest
              ? JSON.stringify(visibleManifestDetail.manifest, null, 2)
              : visibleManifestDetail?.error ?? "No manifest selected."}
          </pre>
        </div>
      </section>

      <section className="mt-6 grid gap-5 lg:grid-cols-3">
        <MixTable title="Retrieval Mix" rows={stats?.retrieval_mode_mix} />
        <SpendTable title="Spend By Adapter" rows={stats?.spend_by_adapter} money />
        <SpendTable title="Spend By Node" rows={stats?.spend_by_node} money />
      </section>
      </main>
    </>
  );
}

function emptyOverview(error: string): ConductorOverview {
  return {
    reachable: false,
    surface: "blox-cockpit.conductor",
    updated_at: new Date().toISOString(),
    error,
  };
}

function emptyContextReport(error: string): ConductorContextReport {
  return {
    reachable: false,
    surface: "blox-cockpit.conductor",
    updated_at: new Date().toISOString(),
    error,
  };
}

function Status({ reachable }: { reachable: boolean }) {
  return (
    <span
      className={[
        "rounded-full px-3 py-1 text-sm font-medium",
        reachable
          ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-200"
          : "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-100",
      ].join(" ")}
    >
      {reachable ? "Cortex connected" : "Degraded"}
    </span>
  );
}

function SectionHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="p-4">
      <h2 className="text-base font-semibold">{title}</h2>
      <p className="mt-1 text-sm text-slate-500">{subtitle}</p>
    </div>
  );
}

function ContextMetric({
  label,
  value,
  detail,
}: {
  label: string;
  value: unknown;
  detail?: string;
}) {
  return (
    <div>
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-xl font-semibold">{display(value)}</div>
      {detail && <div className="mt-1 text-xs text-slate-500">{detail}</div>}
    </div>
  );
}

function CompressionTable({ report }: { report: Record<string, unknown> }) {
  const rows = normalizeCompression(report);
  return (
    <div className="border-t border-slate-200 p-4 dark:border-slate-800">
      <h3 className="mb-2 text-sm font-semibold">Compression</h3>
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="py-2">Zone</th>
            <th className="py-2 text-right">Before</th>
            <th className="py-2 text-right">After</th>
            <th className="py-2 text-right">Ratio</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
          {rows.length ? (
            rows.map((row) => (
              <tr key={row.zone}>
                <td className="py-2 font-medium">{row.zone}</td>
                <td className="py-2 text-right">{display(row.before)}</td>
                <td className="py-2 text-right">{display(row.after)}</td>
                <td className="py-2 text-right">{display(row.ratio)}</td>
              </tr>
            ))
          ) : (
            <tr>
              <td className="py-3 text-slate-500" colSpan={4}>
                No compression rows reported.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function ContextZonesTable({ report }: { report: Record<string, unknown> }) {
  const zones = normalizeZones(report);
  return (
    <div className="border-t border-slate-200 p-4 dark:border-slate-800">
      <h3 className="mb-2 text-sm font-semibold">Context Zones</h3>
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="py-2">Zone</th>
            <th className="py-2 text-right">Tokens</th>
            <th className="py-2 text-right">Items</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
          {zones.length ? (
            zones.map((zone) => (
              <tr key={zone.name}>
                <td className="py-2 font-medium">{zone.name}</td>
                <td className="py-2 text-right">{display(zone.tokens)}</td>
                <td className="py-2 text-right">{display(zone.items)}</td>
              </tr>
            ))
          ) : (
            <tr>
              <td className="py-3 text-slate-500" colSpan={3}>
                No zone breakdown reported.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function ContextQualityPanel({ quality }: { quality: RetrievalQuality }) {
  const qualityRows: Array<[string, unknown]> = [
    ["Mode", quality.mode],
    ["Memory MCP", quality.memory_mcp_version],
    ["Latency", ms(quality.latency_ms)],
    ["Top score", score(quality.top_score)],
    ["Coverage", pct(quality.coverage_ratio ?? undefined)],
    ["Unique sources", quality.unique_source_count],
  ];
  const graphRows: Array<[string, unknown]> = [
    ["Seeds", quality.seed_count],
    ["Neighbours", quality.neighbour_count],
    ["Final chunks", quality.final_count],
    ["Chunks used", quality.chunks_used],
    ["Chunks offered", quality.chunks_offered],
  ];
  return (
    <div className="border-t border-slate-200 p-4 dark:border-slate-800">
      <div className="grid gap-4 md:grid-cols-[0.85fr_1fr]">
        <div>
          <h3 className="mb-2 text-sm font-semibold">Context Quality</h3>
          <dl className="divide-y divide-slate-200 text-sm dark:divide-slate-800">
            {qualityRows.map(([label, value]) => (
              <div key={label} className="flex items-center justify-between gap-3 py-2">
                <dt className="text-slate-500">{label}</dt>
                <dd className="font-medium">{display(value)}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-xs leading-5 text-slate-500">
            Quality here means observable retrieval health: source coverage, score, graph expansion,
            and latency. It does not expose raw prompt text.
          </p>
        </div>
        <div>
          <h3 className="mb-2 text-sm font-semibold">Graph Expansion</h3>
          <dl className="grid grid-cols-2 gap-2 text-sm">
            {graphRows.map(([label, value]) => (
              <div
                key={label}
                className="rounded border border-slate-200 p-3 dark:border-slate-800"
              >
                <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
                <dd className="mt-1 text-lg font-semibold">{display(value)}</dd>
              </div>
            ))}
          </dl>
          <SourceList sources={quality.sources} />
        </div>
      </div>
    </div>
  );
}

function SourceList({ sources }: { sources: string[] }) {
  return (
    <div className="mt-4">
      <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Sources
      </h4>
      {sources.length ? (
        <ul className="max-h-52 space-y-1 overflow-auto text-xs leading-5 text-slate-600 dark:text-slate-400">
          {sources.map((source) => (
            <li key={source} className="truncate" title={source}>
              {source}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-slate-500">No source list reported.</p>
      )}
    </div>
  );
}

function MixTable({ title, rows }: { title: string; rows?: Record<string, number> }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <h2 className="mb-3 text-base font-semibold">{title}</h2>
      <KeyValueRows rows={rows} />
    </div>
  );
}

function SpendTable({
  title,
  rows,
  money = false,
}: {
  title: string;
  rows?: Record<string, number>;
  money?: boolean;
}) {
  const formatted = rows
    ? Object.fromEntries(Object.entries(rows).map(([key, value]) => [key, money ? usd(value) : value]))
    : undefined;
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <h2 className="mb-3 text-base font-semibold">{title}</h2>
      <KeyValueRows rows={formatted} />
    </div>
  );
}

function KeyValueRows({ rows }: { rows?: Record<string, unknown> }) {
  const entries = Object.entries(rows ?? {});
  if (!entries.length) return <p className="text-sm text-slate-500">No rows yet.</p>;
  return (
    <dl className="space-y-2 text-sm">
      {entries.map(([key, value]) => (
        <div key={key} className="flex items-center justify-between gap-3">
          <dt className="truncate text-slate-500">{key}</dt>
          <dd className="font-medium">{display(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function normalizeCompression(report: Record<string, unknown>): CompressionRow[] {
  const compression = report.compression ?? report.compression_rows ?? report.context_compression;
  const compressionObject =
    compression && typeof compression === "object" && !Array.isArray(compression)
      ? (compression as Record<string, unknown>)
      : undefined;
  if (Array.isArray(compressionObject?.zones)) {
    return compressionObject.zones.map((row) => {
      const item = row as Record<string, unknown>;
      return {
        zone: String(item.zone ?? item.name ?? "unknown"),
        before: item.before ?? item.tokens_before,
        after: item.after ?? item.tokens_after,
        ratio: item.ratio ?? item.compression_ratio,
      };
    });
  }
  if (Array.isArray(compression)) {
    return compression.map((row) => {
      const item = row as Record<string, unknown>;
      return {
        zone: String(item.zone ?? item.name ?? "unknown"),
        before: item.before ?? item.tokens_before,
        after: item.after ?? item.tokens_after,
        ratio: item.ratio ?? item.compression_ratio,
      };
    });
  }
  if (compressionObject) {
    return Object.entries(compressionObject).map(([zone, value]) => {
      const row = value as Record<string, unknown>;
      return {
        zone,
        before: row.before ?? row.tokens_before,
        after: row.after ?? row.tokens_after,
        ratio: row.ratio ?? row.compression_ratio,
      };
    });
  }
  const ratio = pick(report, ["compression_ratio"]);
  if (ratio !== undefined) {
    return [
      {
        zone: "retrieved_chunks",
        before: pick(report, ["retrieved_chunk_tokens_before", "retrieved_chunks_before"]),
        after: pick(report, ["retrieved_chunk_tokens_after", "retrieved_chunks_after"]),
        ratio,
      },
      {
        zone: "tool_outputs",
        before: pick(report, ["tool_output_tokens_before", "tool_outputs_before"]),
        after: pick(report, ["tool_output_tokens_after", "tool_outputs_after"]),
        ratio,
      },
    ];
  }
  return [];
}

function normalizeZones(report: Record<string, unknown>) {
  const zones = report.zones;
  if (!Array.isArray(zones)) return [];
  return zones.map((zone) => {
    const row = asRecord(zone);
    return {
      name: String(row.zone ?? row.name ?? row.id ?? "unknown"),
      tokens: row.tokens ?? row.tokens_in_total ?? row.token_count,
      items: row.items ?? row.count ?? row.item_count,
    };
  });
}

function summarizeRetrieval(report: Record<string, unknown>): RetrievalQuality {
  const retrieval = asRecord(report.retrieval);
  const sources = normalizeSources(retrieval.sources);
  const chunksOffered = numberValue(retrieval.chunks_offered);
  const chunksUsed = numberValue(retrieval.chunks_used);
  return {
    mode: stringValue(retrieval.mode) ?? "unknown",
    memory_mcp_version: stringValue(retrieval.memory_mcp_version) ?? undefined,
    latency_ms: numberValue(retrieval.latency_ms) ?? undefined,
    top_score: numberValue(retrieval.top_score) ?? undefined,
    chunks_offered: chunksOffered ?? undefined,
    chunks_used: chunksUsed ?? undefined,
    final_count: numberValue(retrieval.final_count) ?? undefined,
    seed_count: numberValue(retrieval.seed_count) ?? undefined,
    neighbour_count: numberValue(retrieval.neighbour_count) ?? undefined,
    coverage_ratio:
      chunksOffered && chunksUsed !== undefined ? Number((chunksUsed / chunksOffered).toFixed(6)) : null,
    unique_source_count: new Set(sources).size,
    source_count: sources.length,
    sources: [...new Set(sources)].slice(0, 20),
  };
}

function normalizeSources(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (typeof item === "string") return item;
      const row = asRecord(item);
      return stringValue(row.path) ?? stringValue(row.source) ?? stringValue(row.uri) ?? stringValue(row.vault_path);
    })
    .filter((source): source is string => Boolean(source));
}

function summarizeContext(report: Record<string, unknown>) {
  const compressionRows = normalizeCompression(report);
  const compressibleBefore = sumNumbers(compressionRows.map((row) => row.before));
  const compressibleAfter = sumNumbers(compressionRows.map((row) => row.after));
  const fullBefore = pick(report, [
    "context_before_build.tokens_in_total",
    "prompt_tokens_before",
    "tokens_before",
  ]);
  const fullAfter = pick(report, [
    "context_after_build.tokens_in_total",
    "tokens_in_total",
    "context_after_tokens",
    "prompt_tokens_after",
  ]);
  const fixedOverheadAfter =
    typeof fullAfter === "number" && typeof compressibleAfter === "number"
      ? Math.max(fullAfter - compressibleAfter, 0)
      : undefined;
  return {
    compressibleBefore,
    compressibleAfter,
    fullBefore,
    fullAfter,
    fixedOverheadAfter,
    compressibleSaved: savedLabel(compressibleBefore, compressibleAfter),
    fullSaved: savedLabel(fullBefore, fullAfter),
  };
}

function sumNumbers(values: unknown[]) {
  const numbers = values.filter((value): value is number => typeof value === "number");
  if (!numbers.length) return undefined;
  return numbers.reduce((total, value) => total + value, 0);
}

function savedLabel(before: unknown, after: unknown) {
  if (typeof before !== "number" || typeof after !== "number" || before <= 0) return "—";
  const saved = before - after;
  const pctSaved = Math.round((saved / before) * 100);
  return `${saved} saved (${pctSaved}%)`;
}

function pick(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = key.includes(".")
      ? key.split(".").reduce<unknown>((current, part) => {
          if (current && typeof current === "object" && part in current) {
            return (current as Record<string, unknown>)[part];
          }
          return undefined;
        }, record)
      : record[key];
    if (value !== undefined && value !== null) return value;
  }
  return undefined;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function numberValue(value: unknown) {
  return typeof value === "number" ? value : undefined;
}

function display(value: unknown) {
  if (value === undefined || value === null || value === "") return "—";
  if (typeof value === "number") return Number.isInteger(value) ? value.toString() : value.toFixed(3);
  return String(value);
}

function pct(value?: number) {
  if (value === undefined) return "—";
  return `${Math.round(value * 100)}%`;
}

function ms(value?: number) {
  if (value === undefined) return "—";
  return `${Math.round(value)} ms`;
}

function score(value?: number) {
  if (value === undefined) return "—";
  return value.toFixed(3);
}

function pctNumber(value?: number) {
  if (value === undefined) return "—";
  return `${value.toFixed(0)}%`;
}

function usd(value?: number) {
  if (value === undefined) return "—";
  return `$${value.toFixed(4)}`;
}

function usdNumber(value: unknown) {
  return typeof value === "number" ? usd(value) : "—";
}

function stringValue(value: unknown) {
  return typeof value === "string" && value ? value : null;
}
