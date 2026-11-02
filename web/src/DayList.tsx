import { Day } from "./api";
import { percent, shortDate } from "./format";

type Props = {
  symbols: string[];
  symbol: string;
  days: Day[];
  date: string;
  onSymbol: (symbol: string) => void;
  onDate: (date: string) => void;
};

/** Days for one symbol, with a bar showing how far price travelled (high to low, as % of open). */
export function DayList({ symbols, symbol, days, date, onSymbol, onDate }: Props) {
  const mine = days.filter((d) => d.symbol === symbol).sort((a, b) => a.date.localeCompare(b.date));
  const widest = Math.max(...mine.map((d) => d.range_pct), 0.0001);
  return (
    <nav aria-label="Choose a day">
      <div className="symbols" role="group" aria-label="Symbol">
        {symbols.map((s) => (
          <button key={s} className="text-button" aria-pressed={s === symbol} onClick={() => onSymbol(s)}>
            {s}
          </button>
        ))}
      </div>
      <ul className="days">
        {mine.map((d) => (
          <li key={d.date}>
            <button
              className="day"
              aria-current={d.date === date ? "date" : undefined}
              onClick={() => onDate(d.date)}
            >
              <span className="day-name">{shortDate(d.date)}</span>
              <span className="day-range" aria-hidden="true">
                <span style={{ width: `${(d.range_pct / widest) * 100}%` }} />
              </span>
              <span className="day-pct">
                {percent(d.range_pct, 1).replace("+", "")}
                {d.range_rank === 1 && <span className="day-flag"> widest</span>}
              </span>
            </button>
          </li>
        ))}
      </ul>
      <p className="note">The bar is the day's high-to-low range as a share of its opening price.</p>
    </nav>
  );
}
