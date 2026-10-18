import { useEffect, useMemo, useState } from 'react';
import type { EChartsOption } from 'echarts';
import {
  get,
  post,
  type BacktestReport,
  type BarsResponse,
  type CostBps,
  type Instrument,
  type StrategyDef,
  type TrialRow,
} from '../api';
import { AXIS, GRID_LINE, monthlyHeight, monthlyOption } from '../charts';
import { Badge } from '../components/Badge';
import { CandleChart, type ChartMarker } from '../components/CandleChart';
import { Card } from '../components/Card';
import { DataTable } from '../components/DataTable';
import { EChart } from '../components/EChart';
import { Icon } from '../components/Icon';
import { Stat } from '../components/Stat';
import { useToast } from '../components/Toast';
import { int, num, pct, tone, utc } from '../format';
import { useApi } from '../hooks';


/**
 * Equity (log scale) vs buy-and-hold, drawdown and position, stacked with one shared zoom.
 *
 * @param r - Backtest report.
 * @returns ECharts option.
 */
function performanceOption(r: BacktestReport): EChartsOption {
  const t = r.series.time.map((x) => x * 1000);
  const pair = (ys: number[]) => t.map((x, i) => [x, ys[i]]);
  const axisX = (i: number) => ({
    type: 'time' as const,
    gridIndex: i,
    axisLabel: { show: i === 2, color: AXIS },
    axisLine: { lineStyle: { color: '#273244' } },
    splitLine: { show: false },
  });
  return {
    animation: false,
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    legend: { data: ['Strategy', 'Buy & hold'], textStyle: { color: AXIS }, top: 0 },
    grid: [
      { left: 56, right: 16, top: 28, height: '50%' },
      { left: 56, right: 16, top: '64%', height: '15%' },
      { left: 56, right: 16, top: '83%', height: '7%' },
    ],
    xAxis: [axisX(0), axisX(1), axisX(2)],
    yAxis: [
      { type: 'log', gridIndex: 0, axisLabel: { color: AXIS, formatter: (v: number) => `${v.toFixed(1)}x` }, splitLine: { lineStyle: { color: GRID_LINE } } },
      { type: 'value', gridIndex: 1, max: 0, splitNumber: 2, axisLabel: { color: AXIS, formatter: (v: number) => `${Math.round(v * 100)}%` }, splitLine: { lineStyle: { color: GRID_LINE } } },
      { type: 'value', gridIndex: 2, min: -1, max: 1, interval: 1, axisLabel: { color: AXIS } , splitLine: { show: false } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1, 2] },
      { type: 'slider', xAxisIndex: [0, 1, 2], bottom: 2, height: 14, borderColor: '#273244', textStyle: { color: AXIS } },
    ],
    series: [
      { name: 'Strategy', type: 'line', xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, data: pair(r.series.equity), lineStyle: { width: 1.6, color: '#38bdf8' }, itemStyle: { color: '#38bdf8' } },
      { name: 'Buy & hold', type: 'line', xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, data: pair(r.series.benchmark), lineStyle: { width: 1.1, color: '#8a97a8' }, itemStyle: { color: '#8a97a8' } },
      { name: 'Drawdown', type: 'line', xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, data: pair(r.series.drawdown), lineStyle: { width: 1, color: '#f87171' }, areaStyle: { color: 'rgba(248,113,113,0.22)' } },
      { name: 'Position', type: 'line', step: 'end', xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, data: pair(r.series.position), lineStyle: { width: 1, color: '#38bdf8' }, areaStyle: { color: 'rgba(56,189,248,0.16)' } },
    ],
  };
}

/**
 * Backtest Studio: pick an instrument, a strategy and its parameters, run it with realistic costs, and read
 * the full report. Every run is a ledger trial; the trial count is shown next to the results.
 */
export function BacktestStudio() {
  const strategies = useApi<StrategyDef[]>('/backtest/strategies');
  const instruments = useApi<Instrument[]>('/backtest/instruments');
  const presets = useApi<Record<string, CostBps>>('/backtest/presets');
  const runs = useApi<TrialRow[]>('/backtest/runs?limit=40');
  const notify = useToast();

  const [inst, setInst] = useState('');
  const [stratName, setStratName] = useState('trend_sma');
  const [params, setParams] = useState<Record<string, number>>({});
  const [costs, setCosts] = useState('auto');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [includeLockbox, setIncludeLockbox] = useState(false);
  const [report, setReport] = useState<BacktestReport | null>(null);
  const [running, setRunning] = useState(false);
  const [bars, setBars] = useState<BarsResponse | null>(null);

  const strat = strategies.data?.find((s) => s.name === stratName);

  useEffect(() => {
    if (!inst && instruments.data?.length) {
      const spy = instruments.data.find((i) => i.symbol === 'SPY' && i.interval === '1d');
      const pick = spy ?? instruments.data[0];
      setInst(`${pick.source}|${pick.symbol}|${pick.interval}`);
    }
  }, [instruments.data, inst]);

  useEffect(() => {
    if (strat) setParams(Object.fromEntries(Object.entries(strat.params).map(([k, p]) => [k, p.default])));
  }, [strat]);

  useEffect(() => {
    if (!report) return;
    const { source, symbol, interval } = report.meta;
    get<BarsResponse>(`/data/bars?source=${source}&symbol=${symbol}&interval=${interval}&limit=1500`)
      .then(setBars)
      .catch(() => setBars(null));
  }, [report]);

  async function runBacktest() {
    const [source, symbol, interval] = inst.split('|');
    setRunning(true);
    try {
      const r = await post<BacktestReport>('/backtest/run', {
        source, symbol, interval, strategy: stratName, params, costs,
        start: start || null, end: end || null, include_lockbox: includeLockbox,
      });
      setReport(r);
      notify(`Trial ${r.meta.trial_id} recorded. N = ${r.meta.n_trials} on ${r.meta.question}.`, 'ok');
      await runs.reload();
    } catch (err) {
      notify(`Backtest failed: ${String(err)}`, 'bad');
    } finally {
      setRunning(false);
    }
  }

  async function openRun(id: string) {
    try {
      setReport(await get<BacktestReport>(`/backtest/report/${id}`));
    } catch (err) {
      notify(`Could not load report: ${String(err)}`, 'bad');
    }
  }

  const markers = useMemo<ChartMarker[]>(() => {
    if (!report || !bars?.bars.length) return [];
    const first = bars.bars[0].time;
    const out: ChartMarker[] = [];
    for (const t of report.trades) {
      const entry = t.entry ? Date.parse(t.entry) / 1000 : 0;
      const exit = t.exit ? Date.parse(t.exit) / 1000 : 0;
      if (entry >= first) out.push({ time: entry, position: 'belowBar', color: '#34d399', shape: 'arrowUp', text: 'in' });
      if (exit >= first) out.push({ time: exit, position: 'aboveBar', color: '#f87171', shape: 'arrowDown', text: 'out' });
    }
    return out;
  }, [report, bars]);

  const s = report?.stats.strategy ?? {};
  const b = report?.stats.benchmark ?? {};
  const costRows = report
    ? [
        { label: 'Gross CAGR', value: report.costs.gross_cagr },
        { label: 'Commission', value: -report.costs.per_year.commission },
        { label: 'Spread', value: -report.costs.per_year.spread },
        { label: 'Slippage', value: -report.costs.per_year.slippage },
        { label: 'Cash interest', value: report.costs.cash_per_year },
        { label: 'Net CAGR', value: report.costs.net_cagr },
      ]
    : [];
  const costScale = Math.max(...costRows.map((r) => Math.abs(r.value)), 1e-9);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Backtest Studio</h1>
        <span className="muted">Every run is recorded. N rises with each variant you try.</span>
      </div>

      <div className="studio">
        <Card title="Setup">
          <div className="form-grid">
            <label className="field">
              Instrument
              <select value={inst} onChange={(e) => setInst(e.target.value)}>
                {(instruments.data ?? []).map((i) => (
                  <option key={`${i.source}|${i.symbol}|${i.interval}`} value={`${i.source}|${i.symbol}|${i.interval}`}>
                    {i.symbol} · {i.interval} ({i.source}, from {i.first_ts.slice(0, 4)})
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Strategy
              <select value={stratName} onChange={(e) => setStratName(e.target.value)}>
                {(strategies.data ?? []).map((st) => (
                  <option key={st.name} value={st.name}>{st.name}</option>
                ))}
              </select>
            </label>
            {strat && (
              <p className="muted small">
                {strat.description}
                {strat.reference && <span className="faint"> · {strat.reference}</span>}
              </p>
            )}
            {strat &&
              Object.entries(strat.params).map(([k, p]) => (
                <label key={k} className="field" title={p.doc}>
                  {k} <span className="faint">({p.doc})</span>
                  <input
                    type="number"
                    value={params[k] ?? p.default}
                    min={p.low}
                    max={p.high}
                    step={p.step}
                    onChange={(e) => setParams({ ...params, [k]: Number(e.target.value) })}
                  />
                </label>
              ))}
            <label className="field">
              Costs
              <select value={costs} onChange={(e) => setCosts(e.target.value)}>
                <option value="auto">auto (by asset class)</option>
                {Object.entries(presets.data ?? {}).map(([name, c]) => (
                  <option key={name} value={name}>
                    {name}: {c.commission_bps} + {c.spread_bps}/2 + {c.slippage_bps} bps
                  </option>
                ))}
              </select>
            </label>
            <div className="row">
              <label className="field" style={{ flex: 1 }}>
                From
                <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
              </label>
              <label className="field" style={{ flex: 1 }}>
                To
                <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
              </label>
            </div>
            <label className="row small" title="Data from the lockbox date on is sealed. Looking at it spends this question's lockbox for good.">
              <input type="checkbox" checked={includeLockbox} onChange={(e) => setIncludeLockbox(e.target.checked)} />
              <span className={includeLockbox ? 'warn' : 'muted'}>Include the lockbox period (spends it for this question)</span>
            </label>
            <button className="btn primary" onClick={runBacktest} disabled={running || !inst}>
              <Icon name="play" /> {running ? 'Running…' : 'Run backtest'}
            </button>
          </div>
        </Card>

        <div className="stack">
          {!report && <div className="empty">Pick a setup and run a backtest, or open a recent run below.</div>}
          {report && (
            <>
              <div className="note">
                Trial <b className="num">{report.meta.trial_id}</b> on <b>{report.meta.question}</b> ·{' '}
                <b>N = {report.meta.n_trials}</b> trials on this question · {report.meta.start} → {report.meta.end}.
                A good Sharpe after many tries means less; the gauntlet deflates it for N.
                {report.meta.lockbox && (
                  <>
                    {' '}
                    {report.meta.lockbox.included ? (
                      <b className="warn">This run reached into the lockbox (from {report.meta.lockbox.sealed_from}); it is spent for this question.</b>
                    ) : (
                      <>Stops before the lockbox ({report.meta.lockbox.sealed_from}).</>
                    )}
                  </>
                )}
              </div>
              <div className="stats-row">
                <Stat label="CAGR" term="cagr" value={pct(s.cagr, 1, true)} tone={tone(s.cagr)} sub={`buy & hold ${pct(b.cagr, 1, true)}`} />
                <Stat label="Sharpe" term="sharpe" value={num(s.sharpe)} sub={`buy & hold ${num(b.sharpe)} · N = ${report.meta.n_trials}`} />
                <Stat label="Edge Sharpe" term="edge_sharpe" value={num(s.edge_sharpe)} sub={`beta ${num(s.beta)} · alpha ${pct(s.alpha, 1, true)}/yr`} />
                <Stat label="Max drawdown" term="max_dd" value={pct(-s.max_dd)} tone="down" sub={`buy & hold ${pct(-b.max_dd)}`} />
                <Stat label="Volatility" value={pct(s.vol)} sub={`buy & hold ${pct(b.vol)}`} />
                <Stat label="Exposure" term="exposure" value={pct(s.exposure, 0)} sub={`turnover ${num(s.turnover, 1)}/yr`} />
                <Stat label="Trades" value={int(report.stats.n_trades)} sub={`win rate ${pct(report.stats.win_rate, 0)}`} />
              </div>
              <Card title="Performance">
                <EChart option={performanceOption(report)} height={460} />
              </Card>
              <div className="grid cols-2">
                <Card title="Monthly returns">
                  <EChart option={monthlyOption(report.monthly)} height={monthlyHeight(report.monthly)} />
                </Card>
                <Card title="Where the return goes (per year)" term="slippage">
                  <ul className="costbars">
                    {costRows.map((r) => (
                      <li key={r.label}>
                        <span>{r.label}</span>
                        <span className="costbar">
                          <span className={r.value < 0 ? 'neg' : 'pos'} style={{ width: `${(Math.abs(r.value) / costScale) * 100}%` }} />
                        </span>
                        <span className={`num ${tone(r.value)}`}>{pct(r.value, 2, true)}</span>
                      </li>
                    ))}
                  </ul>
                  <p className="faint small">
                    Costs: {report.meta.costs.commission_bps} bps commission + half of {report.meta.costs.spread_bps} bps spread +{' '}
                    {report.meta.costs.slippage_bps} bps slippage per side; turnover {num(report.costs.turnover_per_year, 1)} per year.
                  </p>
                </Card>
              </div>
              <Card title={`Trades on the chart · last ${int(bars?.bars.length)} bars`}>
                {bars?.bars.length ? (
                  <CandleChart bars={bars.bars} markers={markers} intraday={report.meta.interval !== '1d'} height={340} />
                ) : (
                  <div className="empty">Loading bars…</div>
                )}
                <div style={{ marginTop: 12 }}>
                  <DataTable
                    rows={[...report.trades].reverse()}
                    rowKey={(t, i) => `${t.entry}-${i}`}
                    maxHeight={260}
                    columns={[
                      { key: 'entry', label: 'Entry', render: (t) => <span className="num">{t.entry?.slice(0, 10)}</span> },
                      { key: 'exit', label: 'Exit', render: (t) => <span className="num">{t.exit?.slice(0, 10) ?? 'open'}</span> },
                      { key: 'side', label: 'Side', render: (t) => <Badge tone={t.side === 'long' ? 'ok' : 'warn'}>{t.side}</Badge> },
                      { key: 'bars', label: 'Bars', align: 'right', sort: (t) => t.bars, render: (t) => int(t.bars) },
                      { key: 'ret', label: 'Return', align: 'right', sort: (t) => t.ret, render: (t) => <span className={tone(t.ret)}>{pct(t.ret, 1, true)}</span> },
                    ]}
                  />
                </div>
              </Card>
            </>
          )}
          <Card title="Recent runs" term="n_trials">
            <DataTable
              rows={runs.data ?? []}
              rowKey={(t) => t.trial_id}
              maxHeight={300}
              onRowClick={(t) => openRun(t.trial_id)}
              empty="No runs yet."
              columns={[
                { key: 'when', label: 'When', sort: (t) => t.created_at, render: (t) => <span className="num">{utc(t.created_at).slice(5)}</span> },
                { key: 'question', label: 'Question', sort: (t) => t.question },
                { key: 'p', label: 'Params', render: (t) => <span className="faint num">{Object.entries(t.params).map(([k, v]) => `${k}=${v}`).join(' ')}</span> },
                { key: 'sharpe', label: 'Sharpe', align: 'right', sort: (t) => t.sharpe, render: (t) => num(t.sharpe) },
                { key: 'cagr', label: 'CAGR', align: 'right', sort: (t) => t.cagr, render: (t) => <span className={tone(t.cagr)}>{pct(t.cagr, 1, true)}</span> },
                { key: 'dd', label: 'Max DD', align: 'right', sort: (t) => t.max_dd, render: (t) => pct(t.max_dd === null ? null : -t.max_dd) },
              ]}
            />
          </Card>
        </div>
      </div>
    </div>
  );
}
