export const price = (v: number | null | undefined): string =>
  v == null ? "–" : v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const whole = (v: number | null | undefined): string =>
  v == null ? "–" : Math.round(v).toLocaleString("en-US");

export const amount = (v: number | null | undefined): string =>
  v == null ? "–" : v.toLocaleString("en-US", { maximumFractionDigits: v >= 100 ? 0 : 2 });

export const percent = (v: number, digits = 2): string =>
  `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(digits)}%`;

export const ms = (v: number): string => (v >= 100 ? `${Math.round(v).toLocaleString("en-US")} ms` : `${v.toFixed(1)} ms`);

/** Seconds after midnight to HH:MM:SS. */
export function clock(offsetS: number): string {
  const s = Math.min(86_399, Math.max(0, Math.floor(offsetS)));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return [h, m, s % 60].map((n) => String(n).padStart(2, "0")).join(":");
}

export function longDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString("en-US", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function shortDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString("en-US", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
}
