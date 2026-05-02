// Mirrors the cockpit/schemas.py DashboardSnapshot Pydantic shape.

export interface GpuPayload {
  index: number;
  vram_used_mb: number;
  vram_total_mb: number;
  temp_c: number | null;
  power_w: number | null;
  // Sprint 5b: configured power cap (`nvidia-smi --query-gpu=power.limit`).
  // Used for the watts/TDP display + colour. Null when the column was
  // [N/A] or the field isn't present in older snapshots.
  max_power_w: number | null;
}

export interface ModelConfigPayload {
  placement: string;
  keep_alive_mode: string;
  keep_alive_seconds: number | null;
  keep_alive_label: string | null;
  num_ctx_default: number | null;
  single_flight: boolean;
}

export interface ModelActualPayload {
  loaded: boolean;
  vram_mb: number | null;
  expires_at: string | null;
  main_gpu_actual: number | null;
  gpu_layout: Record<string, number> | null;
  mismatch: boolean;
}

export interface ModelMetadataPayload {
  parameter_size: string | null;
  quantization_level: string | null;
  architecture_context_length: number | null;
  release_date: string | null;
  release_date_label: string | null;
  capabilities: string[];
}

export interface ModelContextPayload {
  max_estimated_ctx: number | null;
  max_measured_ctx: number | null;
  estimate_confidence: string;
  headroom_mb: number | null;
}

export interface ModelMetricsPayload {
  cold_load_seconds: number | null;
  warm_load_seconds: number | null;
  throughput_tps: number | null;
  max_ctx_observed: number | null;
  benchmark_profile: string | null;
  placement_tested: string | null;
  gpu_layout_diff: Record<string, number>;
  notes: string | null;
  recommendations: RecommendationPayload[];
  measured_at: string | null;
  age_days: number | null;
  is_stale: boolean;
  staleness: string;
  drift_status: string;
  drift_signals: string[];
  trend_status: string;
  trend_signals: string[];
  trends: Record<string, BenchmarkTrendPayload>;
  profile_status: string;
  data_quality: string;
  retest_recommended: boolean;
  retest_reason: string | null;
  history: BenchmarkHistoryPayload[];
}

export interface BenchmarkHistoryPayload {
  measured_at: string | null;
  cold_load_seconds: number | null;
  warm_load_seconds: number | null;
  throughput_tps: number | null;
  max_ctx_observed: number | null;
  notes: string | null;
  age_days: number | null;
  status: string;
}

export interface BenchmarkTrendPayload {
  direction: "up" | "down" | "flat" | "unknown";
  pct_change: number | null;
  quality: "ok" | "missing" | "volatile";
  latest: number | null;
  baseline: number | null;
}

export interface RecommendationPayload {
  use_case: string;
  score: number;
  confidence: string;
  reasons: string[];
  warnings: string[];
}

export interface ModelCardPayload {
  name: string;
  tag: string | null;
  tag_source: string | null;
  size_bytes: number;
  calls_30d: number;
  metadata: ModelMetadataPayload;
  config: ModelConfigPayload;
  actual: ModelActualPayload;
  context: ModelContextPayload;
  metrics: ModelMetricsPayload | null;
  benchmark_profiles: ModelMetricsPayload[];
}

export interface DashboardSnapshot {
  gpus: GpuPayload[];
  columns: string[];
  models: ModelCardPayload[];
  last_calls: Record<string, unknown>[];
  status: string;
  ts: string;
}

export const COLUMN_LABELS: Record<string, string> = {
  multi_gpu: "Cross GPU",
  on_demand: "On Demand",
};

export function isWarmColumn(col: string): boolean {
  return /^gpu\d+$/.test(col) || col === "multi_gpu";
}

export function fmtBytes(n: number | null): string {
  if (n == null) return "?";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`;
}
