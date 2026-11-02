import { useEffect, useMemo, useState } from "react";
import { Bars, BenchReport, Day, EngineInfo, QueryInfo, getJson } from "./api";
import { DayList } from "./DayList";
import { RacePanel } from "./RacePanel";
import { ReplayChart } from "./ReplayChart";
import { amount, clock, longDate, percent, price, whole } from "./format";
import { useReplay } from "./useReplay";

const SPEEDS = [60, 300, 600, 1000, 3000, 10000];

export default function App() {
  const [days, setDays] = useState<Day[]>([]);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [symbol, setSymbol] = useState<string | null>(null);
  const [date, setDate] = useState<string | null>(null);
  const [bars, setBars] = useState<Bars | null>(null);
  const [queries, setQueries] = useState<QueryInfo[]>([]);
  const [engines, setEngines] = useState<EngineInfo[]>([]);
  const [bench, setBench] = useState<BenchReport | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      getJson<{ symbols: string[]; days: Day[] }>("/api/days"),
      getJson<{ queries: QueryInfo[]; engines: EngineInfo[] }>("/api/queries"),
    ])
      .then(([d, q]) => {
        setDays(d.days);
        setSymbols(d.symbols);
        setQueries(q.queries);
        setEngines(q.engines);
        const first = d.symbols[0];
        setSymbol(first);
        setDate(d.days.find((x) => x.symbol === first && x.range_rank === 1)?.date ?? null);
      })
      .catch((e: Error) => setLoadError(e.message));
    getJson<BenchReport>("/api/bench/results").then(setBench).catch(() => setBench(null));
  }, []);

  useEffect(() => {
    if (!symbol || !date) return;
    setBars(null);
    getJson<Bars>(`/api/days/${symbol}/${date}/bars`).then(setBars).catch((e: Error) => setLoadError(e.message));
  }, [symbol, date]);

  const replay = useReplay(symbol, date);
  const dayOpen = useMemo(() => days.find((d) => d.symbol === symbol && d.date === date)?.open ?? null, [days, symbol, date]);

  const changeSymbol = (next: string) => {
    setSymbol(next);
    setDate(days.find((d) => d.symbol === next && d.range_rank === 1)?.date ?? null);
  };

  if (loadError) {
    return (
      <main className="page">
        <h1>Market Time Machine</h1>
        <p className="error">Could not load data: {loadError}. Is the API running? Start it with `make serve`.</p>
      </main>
    );
  }
  if (!symbol || !date) {
    return (
      <main className="page">
        <h1>Market Time Machine</h1>
        <p className="note">Loading days…</p>
      </main>
    );
  }

  const { frame, offset } = replay;
  const last = frame?.last ?? null;
  const change = last !== null && dayOpen ? last / dayOpen - 1 : null;

  return (
    <main className="page">
      <header className="masthead">
        <h1>Market Time Machine</h1>
        <p className="note">
          Synthetic market data, generated so the demo runs anywhere. Not real prices.
        </p>
      </header>

      <section aria-label="Replay" className="replay">
        <div className="readout">
          <div className="clock-block">
            <div className="clock" aria-live="off">
              {clock(offset)}
              <span className="zone"> UTC</span>
            </div>
            <div className="date">{longDate(date)}</div>
          </div>
          <div className="quote">
            <div className="quote-symbol">{symbol}</div>
            <div className="quote-price">{price(last)}</div>
            <div className={`quote-change ${change === null ? "" : change >= 0 ? "up" : "down"}`}>
              {change === null ? "before the first trade" : `${percent(change)} since the open`}
            </div>
          </div>
          <dl className="stats">
            <div>
              <dt>VWAP so far</dt>
              <dd>{price(frame?.cum_vwap)}</dd>
            </div>
            <div>
              <dt>Volume so far</dt>
              <dd>{amount(frame?.cum_volume)}</dd>
            </div>
            <div>
              <dt>Trades so far</dt>
              <dd>{whole(frame?.cum_trades)}</dd>
            </div>
          </dl>
        </div>

        {bars ? <ReplayChart bars={bars} offsetS={offset} /> : <div className="chart chart-loading">Loading the day…</div>}

        <div className="transport">
          <button
            className="play"
            onClick={replay.playing ? replay.pause : replay.play}
            disabled={replay.status !== "open"}
            aria-label={replay.playing ? "Pause replay" : "Play replay"}
          >
            {replay.playing ? "Pause" : offset >= 86_399 ? "Replay" : "Play"}
          </button>
          <label className="speed">
            <span>Speed</span>
            <select value={replay.speed} onChange={(e) => replay.setSpeed(Number(e.target.value))}>
              {SPEEDS.map((s) => (
                <option key={s} value={s}>
                  {s.toLocaleString("en-US")}×
                </option>
              ))}
            </select>
          </label>
          <div className="scrub">
            <input
              type="range"
              min={0}
              max={86_400}
              step={1}
              value={offset}
              aria-label="Position in the day"
              aria-valuetext={`${clock(offset)} UTC`}
              onChange={(e) => replay.seek(Number(e.target.value))}
            />
            <div className="hours" aria-hidden="true">
              {["00", "03", "06", "09", "12", "15", "18", "21", "24"].map((h) => (
                <span key={h}>{h}</span>
              ))}
            </div>
          </div>
        </div>
        {replay.status === "closed" && (
          <p className="error">The replay connection closed. Reload the page to reconnect.</p>
        )}
      </section>

      <div className="lower">
        <DayList symbols={symbols} symbol={symbol} days={days} date={date} onSymbol={changeSymbol} onDate={setDate} />
        {queries.length > 0 && engines.length > 0 && (
          <RacePanel queries={queries} engines={engines} bench={bench} day={date} />
        )}
      </div>
    </main>
  );
}
