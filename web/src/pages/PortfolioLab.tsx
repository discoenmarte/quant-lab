import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { EChartsOption } from 'echarts';
import {
  get,
  post,
  type GauntletCard,
  type PortfolioReport,
  type PortfolioUniverse,
  type StrategyDef,
  type TrialRow,
} from '../api';
import { AXIS, GRID_LINE, monthlyHeight, monthlyOption } from '../charts';
import { Card } from '../components/Card';
import { DataTable } from '../components/DataTable';
import { EChart } from '../components/EChart';
import { Icon } from '../components/Icon';
import { Stat } from '../components/Stat';
import { useToast } from '../components/Toast';
import { num, pct, tone, utc } from '../format';
import { useApi } from '../hooks';

/** Distinct colours for instruments in the weights chart. */
const PALETTE = ['#38bdf8', '#34d399', '#a78bfa', '#fbbf24', '#f472b6', '#22d3ee', '#fb923c', '#a3e635', '#e879f9',
  '#60a5fa', '#f87171', '#2dd4bf', '#facc15', '#c084fc', '#4ade80', '#fda4af', '#93c5fd', '#fdba74', '#bef264',
  '#67e8f9', '#d8b4fe', '#fcd34d', '#86efac'];

/**
 * Equity (log scale) against equal weight, drawdown and gross exposure, stacked with one shared zoom.
 *
 * @param r - Portfolio report.
 * @returns ECharts option.
 */
function performanceOption(r: PortfolioReport): EChartsOption {
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
    legend: { data: ['Strategy', 'Equal weight'], textStyle: { color: AXIS }, top: 0 },
    grid: [
      { left: 56, right: 16, top: 28, height: '50%' },
      { left: 56, right: 16, top: '64%', height: '15%' },
      { left: 56, right: 16, top: '83%', height: '7%' },
    ],
    xAxis: [axisX(0), axisX(1), axisX(2)],
    yAxis: [
      { type: 'log', gridIndex: 0, axisLabel: { color: AXIS, formatter: (v: number) => `${v.toFixed(1)}x` }, splitLine: { lineStyle: { color: GRID_LINE } } },
      { type: 'value', gridIndex: 1, max: 0, splitNumber: 2, axisLabel: { color: AXIS, formatter: (v: number) => `${Math.round(v * 100)}%` }, splitLine: { lineStyle: { color: GRID_LINE } } },
      { type: 'value', gridIndex: 2, min: 0, axisLabel: { color: AXIS }, splitLine: { show: false } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1, 2] },
      { type: 'slider', xAxisIndex: [0, 1, 2], bottom: 2, height: 14, borderColor: '#273244', textStyle: { color: AXIS } },
    ],
    series: [
      { name: 'Strategy', type: 'line', xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, data: pair(r.series.equity), lineStyle: { width: 1.6, color: '#38bdf8' }, itemStyle: { color: '#38bdf8' } },
      { name: 'Equal weight', type: 'line', xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, data: pair(r.series.benchmark), lineStyle: { width: 1.1, color: AXIS }, itemStyle: { color: AXIS } },
      { name: 'Drawdown', type: 'line', xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, data: pair(r.series.drawdown), lineStyle: { width: 1, color: '#f87171' }, areaStyle: { color: 'rgba(248,113,113,0.22)' } },
      { name: 'Gross exposure', type: 'line', step: 'end', xAxisIndex: 2, yAxisIndex: 2, showSymbol: false, data: pair(r.series.gross), lineStyle: { width: 1, color: '#38bdf8' }, areaStyle: { color: 'rgba(56,189,248,0.16)' } },
    ],
  };
}

/**
 * Weights over time, stacked: positive weights above zero, shorts below.
 *
 * @param r - Portfolio report.
 * @returns ECharts option.
 */
function weightsOption(r: PortfolioReport): EChartsOption {
  const t = r.series.time.map((x) => x * 1000);
  const symbols = Object.keys(r.weights);
  return {
    animation: false,
    tooltip: { trigger: 'axis', valueFormatter: (v) => pct(v as number, 0) },
    legend: { data: symbols, textStyle: { color: AXIS }, top: 0, type: 'scroll' },
    grid: { left: 48, right: 16, top: 30, bottom: 24 },
    xAxis: { type: 'time', axisLabel: { color: AXIS }, splitLine: { show: false } },
    yAxis: { type: 'value', axisLabel: { color: AXIS, formatter: (v: number) => `${Math.round(v * 100)}%` }, splitLine: { lineStyle: { color: GRID_LINE } } },
    dataZoom: [{ type: 'inside' }],
    series: symbols.map((s, i) => ({
      name: s,
      type: 'line' as const,
      stack: 'w',
      stackStrategy: 'samesign' as const,
      showSymbol: false,
      lineStyle: { width: 0 },
      areaStyle: { color: PALETTE[i % PALETTE.length], opacity: 0.85 },
      itemStyle: { color: PALETTE[i % PALETTE.length] },
      data: t.map((x, k) => [x, r.weights[s][k]]),
    })),
  };
}

/**
 * Horizontal bars per instrument, e.g. today's weights or yearly contributions.
 *
 * @param values - Instrument to value.
 * @param asPercent - Format the axis as percentages.
 * @returns ECharts option.
 */
function barsOption(values: Record<string, number>, asPercent = true): EChartsOption {
  const rows = Object.entries(values).sort((a, b) => b[1] - a[1]);
  return {
    animation: false,
    tooltip: { trigger: 'axis', valueFormatter: (v) => pct(v as number, 1) },
    grid: { left: 70, right: 16, top: 6, bottom: 20 },
    xAxis: {
      type: 'value',
      axisLabel: { color: AXIS, formatter: (v: number) => (asPercent ? `${(v * 100).toFixed(Math.abs(v) < 0.1 ? 1 : 0)}%` : String(v)) },
      splitLine: { lineStyle: { color: GRID_LINE } },
    },
    yAxis: { type: 'category', inverse: true, data: rows.map((r) => r[0]), axisLabel: { color: AXIS } },
    series: [{ type: 'bar', data: rows.map((r) => ({ value: r[1], itemStyle: { color: r[1] >= 0 ? '#34d399' : '#f87171' } })) }],
  };
}

/**
 * Portfolio Lab: pick a universe and a diversified strategy, run it against equal weight with realistic costs,
 * read the report, and send it to the gauntlet. Every run is a ledger trial.
 */
export function PortfolioLab() {
  const universes = useApi<PortfolioUniverse[]>('/portfolio/universes');
  const strategies = useApi<StrategyDef[]>('/portfolio/strategies');
  const runs = useApi<TrialRow[]>('/portfolio/runs?limit=40');
  const notify = useToast();
  const navigate = useNavigate();
  const [universe, setUniverse] = useState('multi_asset');
  const [stratName, setStratName] = useState('tsmom');
  const [params, setParams] = useState<Record<string, number>>({});
  const [includeLockbox, setIncludeLockbox] = useState(false);
  const [report, setReport] = useState<PortfolioReport | null>(null);
  const [busy, setBusy] = useState<'' | 'run' | 'gauntlet'>('');

  const strat = strategies.data?.find((s) => s.name === stratName);
  const uni = universes.data?.find((u) => u.name === universe);

  useEffect(() => {
    if (strat) setParams(Object.fromEntries(Object.entries(strat.params).map(([k, p]) => [k, p.default])));
  }, [strat]);

  async function runIt() {
    setBusy('run');
    try {
      const r = await post<PortfolioReport>('/portfolio/run', { universe, strategy: stratName, params, include_lockbox: includeLockbox });
      setReport(r);
      notify(`Trial ${r.meta.trial_id} recorded. N = ${r.meta.n_trials} on ${r.meta.question}.`, 'ok');
      await runs.reload();
    } catch (err) {
      notify(`Run failed: ${String(err)}`, 'bad');
    } finally {
      setBusy('');
    }
  }

  async function judge() {
    setBusy('gauntlet');
    try {
      const card = await post<GauntletCard>('/gauntlet/run', { kind: 'portfolio', universe, strategy: stratName, params });
      notify(`${card.meta.question}: ${card.verdict}${card.failed_stage ? ` at ${card.failed_stage}` : ''}.`, card.verdict === 'rejected' ? 'warn' : 'ok');
      navigate('/gauntlet');
    } catch (err) {
      notify(`Gauntlet failed: ${String(err)}`, 'bad');
    } finally {
      setBusy('');
    }
  }

  async function openRun(id: string) {
    try {
      setReport(await get<PortfolioReport>(`/portfolio/report/${id}`));
    } catch (err) {
      notify(`Could not load report: ${String(err)}`, 'bad');
    }
  }

  const s = report?.stats.strategy ?? {};
  const b = report?.stats.benchmark ?? {};
  return (
    <div className="page">
      <div className="page-head">
        <h1>Portfolio Lab</h1>
        <span className="muted">Diversified rules across asset classes, judged against equal weight of the same funds.</span>
      </div>
      <div className="studio">
        <div className="stack">
          <Card title="Setup">
            <div className="form-grid">
              <label className="field">
                Universe
                <select value={universe} onChange={(e) => setUniverse(e.target.value)}>
                  {(universes.data ?? []).map((u) => (
                    <option key={u.name} value={u.name} disabled={!u.first}>
                      {u.name} ({u.members.length}{u.first ? `, from ${u.first.slice(0, 4)}` : ', not ingested'})
                    </option>
                  ))}
                </select>
              </label>
              {uni && (
                <p className="muted small">
                  {uni.description} <span className="faint">{uni.members.join(' · ')}</span>
                </p>
              )}
              <label className="field">
                Strategy
                <select value={stratName} onChange={(e) => setStratName(e.target.value)}>
                  {(strategies.data ?? []).map((st) => <option key={st.name} value={st.name}>{st.name}</option>)}
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
                    <input type="number" value={params[k] ?? p.default} min={p.low} max={p.high} step={p.step}
                      onChange={(e) => setParams({ ...params, [k]: Number(e.target.value) })} />
                  </label>
                ))}
              <label className="row small" title="Data from the lockbox date on is sealed. Looking at it spends this question's lockbox for good.">
                <input type="checkbox" checked={includeLockbox} onChange={(e) => setIncludeLockbox(e.target.checked)} />
                <span className={includeLockbox ? 'warn' : 'muted'}>Include the lockbox period (spends it for this question)</span>
              </label>
              <div className="row">
                <button className="btn primary" onClick={runIt} disabled={!!busy || !uni?.first}>
                  <Icon name="play" /> {busy === 'run' ? 'Running…' : 'Run'}
                </button>
                <button className="btn" onClick={judge} disabled={!!busy || !uni?.first || !strat || !Object.keys(strat.params).length}>
                  <Icon name="shield" /> {busy === 'gauntlet' ? 'Judging…' : 'Run the gauntlet'}
                </button>
              </div>
            </div>
          </Card>
          <Card title="Recent runs" term="n_trials">
            <DataTable
              rows={runs.data ?? []}
              rowKey={(t) => t.trial_id}
              maxHeight={360}
              onRowClick={(t) => openRun(t.trial_id)}
              empty="No portfolio runs yet."
              columns={[
                { key: 'when', label: 'When', sort: (t) => t.created_at, render: (t) => <span className="num">{utc(t.created_at).slice(5, 16)}</span> },
                { key: 'question', label: 'Question', sort: (t) => t.question },
                { key: 'edge', label: 'Edge', align: 'right', sort: (t) => t.edge_sharpe ?? null, render: (t) => num(t.edge_sharpe) },
              ]}
            />
          </Card>
        </div>

        <div className="stack">
          {!report && <div className="empty">Pick a universe and a strategy and run it, or open a recent run.</div>}
          {report && (
            <>
              <div className="note">
                Trial <b className="num">{report.meta.trial_id}</b> on <b>{report.meta.question}</b> ·{' '}
                <b>N = {report.meta.n_trials}</b> · {report.meta.start} → {report.meta.end} · {report.meta.symbols.length} instruments.{' '}
                {report.meta.lockbox.included ? (
                  <b className="warn">This run reached into the lockbox; it is spent for this question.</b>
                ) : (
                  <>Stops before the lockbox ({report.meta.lockbox.sealed_from}).</>
                )}
              </div>
              <div className="stats-row">
                <Stat label="CAGR" term="cagr" value={pct(s.cagr, 1, true)} tone={tone(s.cagr)} sub={`equal weight ${pct(b.cagr, 1, true)}`} />
                <Stat label="Sharpe" term="sharpe" value={num(s.sharpe)} sub={`equal weight ${num(b.sharpe)}`} />
                <Stat label="Edge Sharpe" term="edge_sharpe" value={num(s.edge_sharpe)} sub={`beta ${num(s.beta)} · alpha ${pct(s.alpha, 1, true)}`} />
                <Stat label="Max drawdown" term="max_dd" value={pct(-s.max_dd)} tone="down" sub={`equal weight ${pct(-b.max_dd)}`} />
                <Stat label="Volatility" value={pct(s.vol)} sub={`equal weight ${pct(b.vol)}`} />
                <Stat label="Gross exposure" value={`${num(s.gross_exposure)}x`} sub={`turnover ${num(s.turnover, 1)}/yr`} />
              </div>
              <Card title="Performance against equal weight">
                <EChart option={performanceOption(report)} height={440} />
              </Card>
              <Card title="Weights over time">
                <EChart option={weightsOption(report)} height={300} />
              </Card>
              <div className="grid cols-2">
                <Card title={`Weights on ${report.meta.end}`}>
                  <EChart option={barsOption(report.latest)} height={Math.max(160, report.meta.symbols.length * 22 + 30)} />
                </Card>
                <Card title="Return contributed per year">
                  <EChart option={barsOption(report.contribution)} height={Math.max(160, report.meta.symbols.length * 22 + 30)} />
                  <p className="faint small">
                    Costs {pct(-report.costs.per_year, 2)}/yr · cash interest {pct(report.costs.cash_per_year, 2, true)}/yr ·
                    turnover {num(report.costs.turnover_per_year, 1)}/yr
                  </p>
                </Card>
              </div>
              <Card title="Monthly returns">
                <EChart option={monthlyOption(report.monthly)} height={monthlyHeight(report.monthly)} />
              </Card>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
