export type Day = {
  symbol: string;
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  n_trades: number;
  range_pct: number;
  range_rank: number;
};

export type Bars = {
  symbol: string;
  date: string;
  minutes: number;
  t0_ms: number;
  n_trades: number;
  t: number[];
  close: number[];
  high: number[];
  low: number[];
  volume: number[];
  cum_vwap: number[];
};

export type Frame = {
  offset_s: number;
  ts: string;
  n: number;
  last: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  volume: number;
  buy_volume: number;
  sell_volume: number;
  max_qty: number;
  cum_trades: number;
  cum_volume: number;
  cum_vwap: number | null;
};

export type QueryInfo = {
  id: string;
  title: string;
  description: string;
  columns: string[];
  default_params: Record<string, unknown>;
};

export type EngineInfo = { id: string; label: string };

export type RaceResult = {
  engine: string;
  label: string;
  median_ms?: number;
  runs_ms?: number[];
  rows?: number;
  preview?: Record<string, unknown>[];
  sql?: string;
  error?: string;
};

export type Race = {
  query: string;
  dataset_rows: number;
  results: RaceResult[];
  agree: boolean;
  disagreements: { engines: string[]; detail: string }[];
  winner: string | null;
  method: string;
};

export type BenchReport = {
  meta: { warm_runs: number; ram_gb: number; logical_cpus: number };
  runs: {
    rows: number;
    engine: string;
    failed?: string;
    queries?: Record<string, { median_s: number }>;
  }[];
};

async function parse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep the status text */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const getJson = <T,>(path: string): Promise<T> => fetch(path).then((r) => parse<T>(r));

export const postJson = <T,>(path: string, body: unknown): Promise<T> =>
  fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => parse<T>(r));

export function replaySocketUrl(symbol: string, date: string): string {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${location.host}/api/replay/${symbol}/${date}`;
}
