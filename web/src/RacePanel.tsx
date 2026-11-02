import { useMemo, useState } from "react";
import { BenchReport, EngineInfo, QueryInfo, Race, postJson } from "./api";
import { ms, whole } from "./format";

const SLOWEST_LANE_SECONDS = 2.6;
const MIN_LANE_SECONDS = 0.15;

const prefersReducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

type Props = {
  queries: QueryInfo[];
  engines: EngineInfo[];
  bench: BenchReport | null;
  day: string;
};

export function RacePanel({ queries, engines, bench, day }: Props) {
  const [queryId, setQueryId] = useState(queries[0]?.id ?? "Q1");
  const [chosen, setChosen] = useState<string[]>(engines.map((e) => e.id));
  const [running, setRunning] = useState(false);
  const [race, setRace] = useState<Race | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [finished, setFinished] = useState<string[]>([]);

  const query = queries.find((q) => q.id === queryId) ?? queries[0];
  const timed = race?.results.filter((r) => r.median_ms !== undefined) ?? [];
  const slowest = Math.max(...timed.map((r) => r.median_ms ?? 0), 0);
  const allDone = timed.length > 0 && timed.every((r) => finished.includes(r.engine));

  const toggle = (id: string) =>
    setChosen((cur) => (cur.includes(id) ? cur.filter((e) => e !== id) : [...cur, id]));

  async function run() {
    setRunning(true);
    setError(null);
    setRace(null);
    setFinished([]);
    try {
      const result = await postJson<Race>("/api/query/run", {
        query: queryId,
        engines: engines.map((e) => e.id).filter((id) => chosen.includes(id)),
        repeats: 3,
        day,
      });
      setRace(result);
      if (prefersReducedMotion()) {
        setFinished(result.results.map((r) => r.engine));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "The race could not run.");
    } finally {
      setRunning(false);
    }
  }

  const recorded = useMemo(() => {
    if (!bench) return null;
    const ok = bench.runs.filter((r) => !r.failed && r.queries);
    const rows = Math.max(...ok.map((r) => r.rows), 0);
    const at = ok.filter((r) => r.rows === rows);
    return { rows, medians: at.map((r) => ({ engine: r.engine, s: r.queries?.[queryId]?.median_s })) };
  }, [bench, queryId]);

  const label = (id: string) => engines.find((e) => e.id === id)?.label ?? id;
  const fastest = timed.find((r) => r.engine === race?.winner);

  return (
    <section aria-labelledby="race-title" className="race">
      <h2 id="race-title">Race the engines</h2>
      <p className="lede">
        Pick a question about the data. Each engine answers it on this server, one at a time, and the
        lanes below take as long as the engines really did.
      </p>

      <fieldset className="queries">
        <legend>Question</legend>
        {queries.map((q) => (
          <label key={q.id} className="choice">
            <input type="radio" name="query" checked={q.id === queryId} onChange={() => setQueryId(q.id)} />
            <span>{q.title}</span>
          </label>
        ))}
      </fieldset>
      {query && <p className="note">{query.description}</p>}

      <fieldset className="engines">
        <legend>Engines</legend>
        {engines.map((e) => (
          <label key={e.id} className="choice" data-engine={e.id}>
            <input type="checkbox" checked={chosen.includes(e.id)} onChange={() => toggle(e.id)} />
            <span className="swatch" aria-hidden="true" />
            <span>{e.label}</span>
          </label>
        ))}
      </fieldset>

      <div className="actions">
        <button className="primary" onClick={run} disabled={running || chosen.length < 2}>
          {running ? "Running…" : race ? "Run again" : "Run the race"}
        </button>
        {chosen.length < 2 && <span className="note">Choose at least two engines.</span>}
      </div>

      <div aria-live="polite" aria-busy={running}>
        {error && <p className="error">{error}</p>}
        {race && (
          <div className="result" key={`${race.query}-${race.results.map((r) => r.median_ms).join()}`}>
            <ul className="lanes">
              {race.results.map((r) => {
                const seconds = r.median_ms
                  ? Math.max(MIN_LANE_SECONDS, (r.median_ms / slowest) * SLOWEST_LANE_SECONDS)
                  : 0;
                const done = finished.includes(r.engine);
                return (
                  <li key={r.engine} className="lane" data-engine={r.engine}>
                    <div className="lane-head">
                      <span className="swatch" aria-hidden="true" />
                      <span className="lane-name">{r.label}</span>
                      <span className="lane-time">
                        {r.error ? "failed" : done && r.median_ms !== undefined ? ms(r.median_ms) : ""}
                        {done && r.engine === race.winner && <span className="flag"> fastest</span>}
                      </span>
                    </div>
                    <div className="track">
                      {!r.error && (
                        <div
                          className={`runner${done ? " done" : ""}`}
                          style={{ animationDuration: `${seconds}s` }}
                          onAnimationEnd={() => setFinished((cur) => [...cur, r.engine])}
                        />
                      )}
                    </div>
                    {r.error && <p className="error">{r.error}</p>}
                  </li>
                );
              })}
            </ul>

            {allDone && (
              <div className="verdict">
                <p>
                  {race.agree
                    ? "Every engine returned exactly the same answer."
                    : "The engines disagreed. Treat these timings as invalid."}{" "}
                  {fastest && slowest > 0 && fastest.median_ms
                    ? `${fastest.label} was ${(slowest / fastest.median_ms).toFixed(1)} times faster than the slowest.`
                    : ""}
                </p>
                <p className="note">
                  {whole(race.dataset_rows)} rows. {race.method}.
                </p>
                {race.results[0]?.preview && race.results[0].preview.length > 0 && (
                  <PreviewTable rows={race.results[0].preview} total={race.results[0].rows ?? 0} />
                )}
                {race.results.find((r) => r.sql) && (
                  <details>
                    <summary>Show the SQL</summary>
                    <pre>{race.results.find((r) => r.sql)?.sql}</pre>
                  </details>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {recorded && recorded.rows > 0 && (
        <div className="recorded">
          <h3>Recorded on {whole(recorded.rows)} rows</h3>
          <p className="note">
            Median of {bench?.meta.warm_runs} warm runs, same question, measured earlier on a{" "}
            {bench?.meta.logical_cpus}-CPU machine.
          </p>
          <ul>
            {recorded.medians.map((m) => (
              <li key={m.engine} data-engine={m.engine}>
                <span className="swatch" aria-hidden="true" />
                <span className="lane-name">{label(m.engine)}</span>
                <span className="lane-time">{m.s === undefined ? "–" : ms(m.s * 1000)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function PreviewTable({ rows, total }: { rows: Record<string, unknown>[]; total: number }) {
  const cols = Object.keys(rows[0]);
  const show = (v: unknown) => {
    if (typeof v === "number") {
      return Number.isInteger(v)
        ? v.toLocaleString("en-US")
        : v.toLocaleString("en-US", { maximumFractionDigits: 4 });
    }
    // timestamps arrive as ISO strings; show them as "2025-01-02 00:05"
    return typeof v === "string" && /^\d{4}-\d{2}-\d{2}T/.test(v) ? v.slice(0, 16).replace("T", " ") : String(v);
  };
  return (
    <div className="preview">
      <table>
        <caption>
          First {rows.length} of {whole(total)} result rows
        </caption>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c} scope="col">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c}>{show(r[c])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
