import { Badge } from '../components/Badge';
import { Card } from '../components/Card';
import { useApi } from '../hooks';

/** One rule of the intraday library. */
interface IntradayRule {
  name: string;
  description: string;
  reference: string;
  grid: number;
  params: Record<string, { default: number; low: number; high: number; doc: string }>;
}

/** A noise or power row of the hourly calibration study. */
interface CalibrationRow {
  family: string;
  scenario: string;
  n: number;
  admitted: number;
  rate: number;
  low: number;
  high: number;
  true_edge?: number;
  years: number;
}

/** The latest finished UTC day's facts for one coin (no rule is run). */
interface Reading {
  symbol: string;
  day: string;
  volatile: boolean;
  realised_vol: number;
  median_vol: number;
  first_hour: number | null;
  range_high_4h: number;
}

/** A gauntlet card of an intraday question. */
interface IntradayCard {
  card_id: string;
  created_at: string;
  question: string;
  verdict: string;
  stages: { key: string; passed: boolean | null }[];
}

/** Body of `GET /api/intraday`. */
interface IntradayOverview {
  rules: IntradayRule[];
  round_trip_bps: number;
  calibration: { id: string; created_at: string; headline: string; noise: CalibrationRow[]; power: CalibrationRow[] } | null;
  cards: IntradayCard[];
  readings: Reading[];
}

const pct = (x: number | null, d = 2) => (x === null || !Number.isFinite(x) ? '–' : `${(100 * x).toFixed(d)}%`);

/**
 * Intraday Cockpit (M9): the intraday library on hourly BTC and ETH, its calibration on synthetic hourly markets,
 * the gauntlet's verdicts, and the selector's reading for the latest finished UTC day. Nothing here trades: live
 * intraday decisions need a live feed, built only if a rule passes (decision 0015).
 */
export function IntradayCockpit() {
  const { data, error } = useApi<IntradayOverview>('/intraday', 60000);
  if (error) return <div className="page"><div className="empty">API unreachable: {error}</div></div>;
  if (!data) return <div className="page"><div className="empty">Loading…</div></div>;
  const cal = data.calibration;
  return (
    <div className="page">
      <div className="page-head">
        <h1>Intraday Cockpit</h1>
        <span className="muted">Rules that enter and leave within one UTC day, on hourly BTC and ETH. Every traded day costs a {data.round_trip_bps.toFixed(0)} bps round trip.</span>
      </div>
      <div className="grid cols-2">
        <Card title="Morning reading (latest finished UTC day)">
          {data.readings.length === 0 && <div className="empty">No hourly bars stored.</div>}
          <ul className="gate-items">
            {data.readings.map((r) => (
              <li key={r.symbol}>
                <span className={`gate-mark ${r.volatile ? 'ok' : 'wait'}`}>{r.volatile ? '●' : '○'}</span>
                <span>
                  <b>{r.symbol}</b> {r.day}: {r.volatile ? 'more volatile than usual, so the selector would trade the next day' : 'calmer than usual, so the selector would stand aside the next day'}
                  <span className="muted gate-detail">
                    hourly volatility {pct(r.realised_vol)} against a 60-day median of {pct(r.median_vol)}; first hour {pct(r.first_hour)}; first four hours' high {r.range_high_4h.toLocaleString()}
                  </span>
                </span>
              </li>
            ))}
          </ul>
          <p className="muted gate-detail">These are facts about the day, not a rule's result, so they count as no trial. Live decisions need a live price feed, which is built only if a rule passes.</p>
        </Card>
        <Card title="Calibration on synthetic hourly markets" term="calibration">
          {!cal && <div className="empty">Not run yet: <code>qlab calibrate --hourly-reps 100</code>.</div>}
          {cal && (
            <>
              <p className="gate-next">{cal.headline}</p>
              <table className="table">
                <thead><tr><th>Family</th><th>Researcher</th><th>Passed</th><th>Rate</th></tr></thead>
                <tbody>
                  {cal.noise.map((n) => (
                    <tr key={`${n.family}-${n.scenario}`}>
                      <td>{n.family.replace('hourly/', '')}</td>
                      <td>{n.scenario === 'noise_fixed' ? 'fixed in advance' : 'searched the grid'}</td>
                      <td className="num">{n.admitted}/{n.n}</td>
                      <td className="num"><Badge tone={n.rate <= 0.05 ? 'ok' : 'bad'}>{pct(n.rate, 1)}</Badge></td>
                    </tr>
                  ))}
                  {cal.power.map((p) => (
                    <tr key={`${p.family}-${p.true_edge}`}>
                      <td>{p.family.replace('hourly/', '')}</td>
                      <td>planted edge {p.true_edge?.toFixed(2)}</td>
                      <td className="num">{p.admitted}/{p.n}</td>
                      <td className="num">{pct(p.rate, 0)} found</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </Card>
      </div>
      <Card title="The library">
        <div className="grid cols-2">
          {data.rules.map((r) => (
            <div key={r.name} className="stack">
              <h3 style={{ margin: 0 }}>{r.name}</h3>
              <p style={{ margin: 0 }}>{r.description}</p>
              <p className="muted gate-detail">{r.reference} · grid of {r.grid} settings, every one a ledger trial</p>
              <ul className="plain-list">
                {Object.entries(r.params).map(([k, p]) => (
                  <li key={k}><code>{k}</code> {p.doc}: default {p.default}, from {p.low} to {p.high}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </Card>
      <Card title="Verdicts">
        {data.cards.length === 0 && <div className="empty">No intraday card yet. The pre-registered runs (docs/preregistration/2026-10-22-m9-intraday-crypto.md) wait for the calibration.</div>}
        {data.cards.length > 0 && (
          <table className="table">
            <thead><tr><th>Question</th><th>Verdict</th><th>First failed stage</th><th>Card</th></tr></thead>
            <tbody>
              {data.cards.map((c) => (
                <tr key={c.card_id}>
                  <td>{c.question}</td>
                  <td><Badge tone={c.verdict === 'validated' ? 'ok' : c.verdict === 'candidate' ? 'warn' : 'bad'}>{c.verdict}</Badge></td>
                  <td>{c.stages.find((s) => s.passed === false)?.key ?? '–'}</td>
                  <td><a href={`/gauntlet?card=${c.card_id}`}>{c.card_id}</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
