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
  keep_alive_seconds: number | null;
  num_ctx_default: number | null;
  single_flight: boolean;
}

export interface ModelActualPayload {
  loaded: boolean;
  vram_mb: number | null;
  main_gpu_actual: number | null;
  mismatch: boolean;
}

export interface ModelMetricsPayload {
  cold_load_seconds: number | null;
  throughput_tps: number | null;
  max_ctx_observed: number | null;
  measured_at: string | null;
}

export interface ModelCardPayload {
  name: string;
  tag: string | null;
  size_bytes: number;
  config: ModelConfigPayload;
  actual: ModelActualPayload;
  metrics: ModelMetricsPayload | null;
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
  multi_gpu: "Multi-GPU",
  on_demand: "On Demand",
  available: "Available",
};

export const WARM_COLUMNS = new Set([
  "gpu0",
  "gpu1",
  "gpu2",
  "gpu3",
  "multi_gpu",
]);

export function fmtBytes(n: number | null): string {
  if (n == null) return "?";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`;
}
