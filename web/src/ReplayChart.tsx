import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { Bars } from "./api";
import { price } from "./format";

const INK = "#16202a";
const DAY_LINE = "#c3ccd6";
const VWAP = "#6250d6";
const MUTED = "#566472";
const GRID = "#e3e8ed";

const utcHm = (sec: number, t0?: number) =>
  t0 !== undefined && sec - t0 >= 86_400 ? "24:00" : new Date(sec * 1000).toISOString().slice(11, 16);

/** Price over one day. The whole day is drawn faintly; the part already replayed is drawn in ink. */
export function ReplayChart({ bars, offsetS }: { bars: Bars; offsetS: number }) {
  const host = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const offsetRef = useRef(offsetS);
  offsetRef.current = offsetS;

  const t0 = bars.t0_ms / 1000;
  const width = bars.minutes * 60;

  // Build the plot once per day, and rebuild it when the container is resized.
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const lo = Math.min(...bars.low);
    const hi = Math.max(...bars.high);
    const pad = (hi - lo) * 0.08 || 1;
    const xs = bars.t.map((t) => t / 1000);

    const opts = (w: number): uPlot.Options => ({
      width: w,
      height: Math.max(260, Math.min(380, Math.round(w * 0.3))),
      pxAlign: false,
      legend: { live: true },
      cursor: { drag: { x: false, y: false }, points: { size: 8, width: 2 } },
      scales: {
        x: { time: true, range: [t0, t0 + 86_400] },
        y: { range: [lo - pad, hi + pad] },
      },
      axes: [
        {
          stroke: MUTED,
          grid: { stroke: GRID, width: 1 },
          ticks: { stroke: GRID, width: 1 },
          // fewer labels on narrow screens so they never run together
          splits: (u) => {
            const step = u.width < 560 ? 6 : 3;
            return Array.from({ length: 24 / step + 1 }, (_, i) => t0 + i * step * 3600);
          },
          values: (_u, splits) => splits.map((v) => utcHm(v, t0)),
          font: "12px 'Geist Variable', system-ui, sans-serif",
        },
        {
          stroke: MUTED,
          grid: { stroke: GRID, width: 1 },
          ticks: { show: false },
          size: 76,
          values: (_u, splits) => splits.map((v) => price(v).replace(/\.00$/, "")),
          font: "12px 'Geist Variable', system-ui, sans-serif",
        },
      ],
      series: [
        { label: "Time (UTC)", value: (_u, v) => (v == null ? "–" : utcHm(v)) },
        { label: "Day", stroke: DAY_LINE, width: 1.5, points: { show: false }, spanGaps: false },
        {
          label: "Price",
          stroke: INK,
          width: 2,
          points: { show: false },
          value: (_u, v) => (v == null ? "–" : price(v)),
        },
        {
          label: "VWAP",
          stroke: VWAP,
          width: 1.75,
          dash: [6, 4],
          points: { show: false },
          value: (_u, v) => (v == null ? "–" : price(v)),
        },
      ],
      hooks: {
        draw: [
          (u) => {
            const x = Math.round(u.valToPos(t0 + offsetRef.current, "x", true));
            const { ctx } = u;
            ctx.save();
            ctx.setLineDash([]); // uPlot leaves the VWAP dash pattern set when this hook runs
            ctx.strokeStyle = INK;
            ctx.lineWidth = Math.max(1, devicePixelRatio);
            ctx.beginPath();
            ctx.moveTo(x, u.bbox.top);
            ctx.lineTo(x, u.bbox.top + u.bbox.height);
            ctx.stroke();
            ctx.restore();
          },
        ],
      },
    });

    const blank = (): uPlot.AlignedData => [xs, bars.close, xs.map(() => null), xs.map(() => null)];
    const u = new uPlot(opts(el.clientWidth), blank(), el);
    plot.current = u;
    const observer = new ResizeObserver(() => {
      u.setSize({ width: el.clientWidth, height: opts(el.clientWidth).height });
    });
    observer.observe(el);
    return () => {
      observer.disconnect();
      u.destroy();
      plot.current = null;
    };
  }, [bars, t0]);

  // Reveal the replayed part as the clock moves.
  useEffect(() => {
    const u = plot.current;
    if (!u) return;
    const upto = Math.min(bars.t.length - 1, Math.floor(offsetS / width));
    const xs = bars.t.map((t) => t / 1000);
    const shown = (series: number[]) => series.map((v, i) => (i <= upto && offsetS > 0 ? v : null));
    u.setData([xs, bars.close, shown(bars.close), shown(bars.cum_vwap)] as uPlot.AlignedData);
  }, [bars, offsetS, width]);

  return <div ref={host} className="chart" role="img" aria-label={`${bars.symbol} price on ${bars.date}`} />;
}
