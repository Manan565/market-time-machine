"""Render benchmark results: python -m mtm.plot chart|table [--results FILE] [--out FILE]

Needs matplotlib (`pip install -e ".[plot]"`). The chart uses a log axis because query times
span about three orders of magnitude (milliseconds to seconds); every bar carries its value, and
`table` prints the same numbers as Markdown so nothing depends on color alone.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mtm.queries import QUERIES

SHORT = {
    "Q1": "Q1  VWAP per 5 min",
    "Q2": "Q2  1-minute OHLC",
    "Q3": "Q3  rolling 1h volatility",
    "Q4": "Q4  as-of join",
    "Q5": "Q5  top 10 trades, 4 h",
    "Q6": "Q6  10-second slice",
}
ENGINE_LABEL = {
    "pandas": "pandas (in memory)",
    "duckdb": "DuckDB (reads Parquet each query)",
    "duckdb-mem": "DuckDB (in-memory table)",
}
# Categorical slots 1-3 of the reference palette; validated for three series, light surface.
COLORS = {"pandas": "#2a78d6", "duckdb": "#eb6834", "duckdb-mem": "#1baf7a"}
SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"


def _by_size(report: dict) -> dict[int, dict[str, dict]]:
    out: dict[int, dict[str, dict]] = {}
    for run in report["runs"]:
        if "failed" not in run:
            out.setdefault(run["rows"], {})[run["engine"]] = run
    return dict(sorted(out.items()))


def _fmt_ms(seconds: float) -> str:
    ms = seconds * 1000
    return f"{ms:,.0f} ms" if ms >= 100 else f"{ms:.1f} ms"


def winners(report: dict, rows: int) -> dict[str, str]:
    """The engine with the lowest median per query at a given size."""
    runs = _by_size(report)[rows]
    return {
        q: min(runs, key=lambda e: runs[e]["queries"][q]["median_s"]) for q in QUERIES
    }


def markdown_table(report: dict) -> str:
    sizes = _by_size(report)
    lines = []
    for rows, runs in sizes.items():
        engines = [e for e in ENGINE_LABEL if e in runs]
        lines += [
            f"**{rows:,} BTCUSDT rows** (median of {report['meta']['warm_runs']} warm runs)",
            "",
            "| Query | " + " | ".join(ENGINE_LABEL[e] for e in engines) + " |",
            "| --- | " + " | ".join("---:" for _ in engines) + " |",
        ]
        best = winners(report, rows)
        for q in QUERIES:
            cells = []
            for e in engines:
                text = _fmt_ms(runs[e]["queries"][q]["median_s"])
                cells.append(f"**{text}**" if best[q] == e else text)
            lines.append(f"| {SHORT[q]} | " + " | ".join(cells) + " |")
        lines.append(
            "| *One-off load* | "
            + " | ".join(f"*{runs[e]['load_s']:.2f} s*" for e in engines)
            + " |"
        )
        lines.append(
            "| *Peak memory* | "
            + " | ".join(f"*{runs[e]['peak_rss_mb']:,.0f} MB*" for e in engines)
            + " |"
        )
        lines.append("")
    return "\n".join(lines)


def render_chart(report: dict, out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sizes = _by_size(report)
    queries = list(QUERIES)
    engines = [e for e in ENGINE_LABEL if all(e in r for r in sizes.values())]
    top = max(
        r[e]["queries"][q]["median_s"] * 1000 for r in sizes.values() for e in engines for q in queries
    )
    bar_h, gap = 0.24, 0.02

    plt.rcParams.update({"font.family": "DejaVu Sans", "text.color": INK})
    fig, axes = plt.subplots(1, len(sizes), figsize=(12.5, 6.6), sharey=True, facecolor=SURFACE)
    for ax, (rows, runs) in zip(axes, sizes.items(), strict=True):
        ax.set_facecolor(SURFACE)
        best = winners(report, rows)
        for qi, q in enumerate(queries):
            for ei, e in enumerate(engines):
                ms = runs[e]["queries"][q]["median_s"] * 1000
                y = qi + (ei - (len(engines) - 1) / 2) * (bar_h + gap)
                ax.barh(y, ms - 0.5, left=0.5, height=bar_h, color=COLORS[e],
                        edgecolor=SURFACE, linewidth=1.2)  # fmt: skip
                ax.text(
                    ms * 1.12, y, _fmt_ms(ms / 1000), va="center", fontsize=9.5,
                    color=INK, fontweight="bold" if best[q] == e else "normal",
                )  # fmt: skip
        ax.set_xscale("log")
        ax.set_xlim(0.5, top * 5)
        ax.set_ylim(len(queries) - 0.5, -0.5)
        ax.set_yticks(range(len(queries)), [SHORT[q] for q in queries], fontsize=10.5)
        ax.set_xticks([1, 10, 100, 1000], ["1 ms", "10 ms", "100 ms", "1 s"], fontsize=10)
        ax.minorticks_off()
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.tick_params(axis="both", length=0, colors=MUTED)
        ax.set_title(f"{rows:,} rows", loc="left", fontsize=12, fontweight="bold", pad=10)
        ax.set_xlabel("median query time, log scale (shorter is faster)", fontsize=10, color=MUTED)

    top_rows = list(sizes)[-1]
    won = [e for e in winners(report, top_rows).values() if e == "duckdb-mem"]
    fig.suptitle(
        f"At {top_rows // 1_000_000}M rows, in-memory DuckDB wins {len(won)} of {len(queries)} queries; "
        "pandas keeps the as-of join and the tiny slice",
        x=0.01, ha="left", fontsize=13.5, fontweight="bold", y=0.985,
    )  # fmt: skip
    handles = [plt.Rectangle((0, 0), 1, 1, color=COLORS[e]) for e in engines]
    fig.legend(handles, [ENGINE_LABEL[e] for e in engines], loc="upper left",
               bbox_to_anchor=(0.01, 0.945), ncol=len(engines), frameon=False, fontsize=10.5)  # fmt: skip
    fig.text(
        0.01, 0.012,
        "Synthetic data. 2 vCPUs, DuckDB uses both, pandas one. Bold = fastest per query. "
        "One-off load times are in the table below, not in these bars.",
        fontsize=9, color=MUTED,
    )  # fmt: skip
    fig.tight_layout(rect=(0, 0.03, 1, 0.92))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=170, facecolor=SURFACE)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mtm.plot")
    parser.add_argument("command", choices=["chart", "table"])
    parser.add_argument("--results", default="bench/results/week2_synthetic.json")
    parser.add_argument("--out", default="docs/bench_medians.png")
    args = parser.parse_args(argv)
    report = json.loads(Path(args.results).read_text())
    if args.command == "chart":
        render_chart(report, Path(args.out))
        print(f"wrote {args.out}")
    else:
        print(markdown_table(report))


if __name__ == "__main__":
    main()
