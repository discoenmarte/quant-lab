import { useEffect, useState } from 'react';
import type { EChartsOption } from 'echarts';
import {
  get,
  post,
  type CardRow,
  type CostBps,
  type FunnelRow,
  type GauntletCard,
  type Instrument,
  type LockboxState,
  type PortfolioUniverse,
  type StrategyDef,
  type ThresholdState,
} from '../api';
import { Badge } from '../components/Badge';
import { CalibrationPanel } from '../components/CalibrationPanel';
import { Card } from '../components/Card';
import { DataTable } from '../components/DataTable';
import { EChart } from '../components/EChart';
import { Icon } from '../components/Icon';
import { ReportCard, stageTone, VerdictBadge } from '../components/ReportCard';
import { useToast } from '../components/Toast';
import { int, utc } from '../format';
import { useApi } from '../hooks';

const AXIS = '#8a97a8';
const GRID_LINE = '#151c26';

/** What each threshold means, for the Thresholds tab. */
const THRESHOLD_DOCS: Record<string, string> = {
  lockbox_from: 'First sealed date for the whole lab',
  min_net_sharpe: 'Costs: edge Sharpe after costs must exceed',
  wf_min_oos_sharpe: 'Walk-forward: out-of-sample edge Sharpe must exceed',
  wf_min_ratio: 'Walk-forward: share of in-sample edge kept',
  plateau_min: 'Plateau: share of neighbours that also work',
  perm_max_p: 'Permutation: p-value at most',
  n_perm: 'Permutation: shuffled histories',
  n_boot: 'Bootstrap: resampled paths',
  boot_block: 'Bootstrap: mean block length, days',
  dd_budget: 'Bootstrap: drawdown budget for sizing',
  dsr_min: 'Significance: Deflated Sharpe at least',
  pbo_max: 'Significance: PBO at most',
  fdr_prior: 'False discovery: prior share of real ideas',
  fdr_sr1: 'False discovery: Sharpe of a real edge',
  regime_min_share: 'Regimes: share with a positive edge (or net return, by the basis)',
  max_corr: 'Diversification: correlation to validated portfolio at most',
  grid_max: 'Grid: most settings per run',
  dsr_variance: 'Significance: spread of trial Sharpes (sample, upper_half, mad, luck_bound, luck_max)',
  regime_basis: 'Regimes: judge the edge or the net return in each regime (edge, net)',
};

/**
 * Funnel: how many report cards reached and passed each stage.
 *
 * @param rows - Funnel rows from the API.
 * @returns ECharts option.
 */
function funnelOption(rows: FunnelRow[]): EChartsOption {
  return {
    animation: false,
    tooltip: { trigger: 'axis' },
    legend: { data: ['Passed', 'Failed', 'Informational'], textStyle: { color: AXIS }, top: 0 },
    grid: { left: 150, right: 16, top: 26, bottom: 10 },
    xAxis: { type: 'value', axisLabel: { color: AXIS }, splitLine: { lineStyle: { color: GRID_LINE } } },
    yAxis: { type: 'category', inverse: true, data: rows.map((r) => r.name), axisLabel: { color: AXIS } },
    series: [
      { name: 'Passed', type: 'bar', stack: 'a', data: rows.map((r) => r.passed), itemStyle: { color: 'rgba(52,211,153,0.75)' } },
      { name: 'Failed', type: 'bar', stack: 'a', data: rows.map((r) => r.failed), itemStyle: { color: 'rgba(248,113,113,0.75)' } },
      {
        name: 'Informational',
        type: 'bar',
        stack: 'a',
        data: rows.map((r) => r.reached - r.passed - r.failed),
        itemStyle: { color: '#273244' },
      },
    ],
  };
}

/**
 * Run form: instrument, strategy, candidate parameters and costs.
 *
 * @param props.onCard - Receives the new report card.
 */
function RunForm({ onCard }: { onCard: (c: GauntletCard) => void }) {
  const [kind, setKind] = useState<'bars' | 'portfolio'>('bars');
  const strategies = useApi<StrategyDef[]>(kind === 'portfolio' ? '/portfolio/strategies' : '/backtest/strategies');
  const universes = useApi<PortfolioUniverse[]>('/portfolio/universes');
  const [universe, setUniverse] = useState('multi_asset');
  const instruments = useApi<Instrument[]>('/backtest/instruments');
  const presets = useApi<Record<string, CostBps>>('/backtest/presets');
  const lock = useApi<LockboxState>('/gauntlet/lockbox');
  const notify = useToast();
  const [inst, setInst] = useState('');
  const [stratName, setStratName] = useState('trend_sma');

  useEffect(() => {
    setStratName(kind === 'portfolio' ? 'tsmom' : 'trend_sma');
  }, [kind]);
  const [params, setParams] = useState<Record<string, number>>({});
  const [costs, setCosts] = useState('auto');
  const [running, setRunning] = useState(false);
  const strat = strategies.data?.find((s) => s.name === stratName);

  useEffect(() => {
    if (!inst && instruments.data?.length) {
      const spy = instruments.data.find((i) => i.symbol === 'SPY' && i.interval === '1d') ?? instruments.data[0];
      setInst(`${spy.source}|${spy.symbol}|${spy.interval}`);
    }
  }, [instruments.data, inst]);

  useEffect(() => {
    if (strat) setParams(Object.fromEntries(Object.entries(strat.params).map(([k, p]) => [k, p.default])));
  }, [strat]);

  const [, symbol, interval] = inst.split('|');
  const question = !strat ? '' : kind === 'portfolio' ? `${strat.family}:${universe}:1d` : `${strat.family}:${symbol}:${interval}`;
  const spent = lock.data?.spent.some((s) => s.question === question);

  async function runIt() {
    const [source, sym, intv] = inst.split('|');
    setRunning(true);
    try {
      const body = kind === 'portfolio'
        ? { kind, universe, strategy: stratName, params }
        : { kind, source, symbol: sym, interval: intv, strategy: stratName, params, costs };
      const card = await post<GauntletCard>('/gauntlet/run', body);
      notify(`${card.meta.question}: ${card.verdict}${card.failed_stage ? ` at ${card.failed_stage}` : ''}.`, card.verdict === 'rejected' ? 'warn' : 'ok');
      onCard(card);
      await lock.reload();
    } catch (err) {
      notify(`Gauntlet failed: ${String(err)}`, 'bad');
    } finally {
      setRunning(false);
    }
  }

  return (
    <Card title="Judge a candidate">
      <div className="form-grid">
        <div className="tabs">
          <button className={`tab ${kind === 'bars' ? 'active' : ''}`} onClick={() => setKind('bars')}>Single instrument</button>
          <button className={`tab ${kind === 'portfolio' ? 'active' : ''}`} onClick={() => setKind('portfolio')}>Portfolio</button>
        </div>
        {kind === 'bars' ? (
          <label className="field">
            Instrument
            <select value={inst} onChange={(e) => setInst(e.target.value)}>
              {(instruments.data ?? []).map((i) => (
                <option key={`${i.source}|${i.symbol}|${i.interval}`} value={`${i.source}|${i.symbol}|${i.interval}`}>
                  {i.symbol} · {i.interval} ({i.source})
                </option>
              ))}
            </select>
          </label>
        ) : (
          <label className="field">
            Universe
            <select value={universe} onChange={(e) => setUniverse(e.target.value)}>
              {(universes.data ?? []).map((u) => (
                <option key={u.name} value={u.name} disabled={!u.first}>
                  {u.name} ({u.members.length}{u.first ? `, from ${u.first.slice(0, 4)}` : ''})
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          Strategy
          <select value={stratName} onChange={(e) => setStratName(e.target.value)}>
            {(strategies.data ?? []).filter((s) => Object.keys(s.params).length).map((s) => (
              <option key={s.name} value={s.name}>{s.name}</option>
            ))}
          </select>
        </label>
        {strat &&
          Object.entries(strat.params).map(([k, p]) => (
            <label key={k} className="field" title={p.doc}>
              {k} <span className="faint">({p.doc})</span>
              <input type="number" value={params[k] ?? p.default} min={p.low} max={p.high} step={p.step}
                onChange={(e) => setParams({ ...params, [k]: Number(e.target.value) })} />
            </label>
          ))}
        {kind === 'bars' && (
          <label className="field">
            Costs
            <select value={costs} onChange={(e) => setCosts(e.target.value)}>
              <option value="auto">auto (by asset class)</option>
              {Object.keys(presets.data ?? {}).map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
          </label>
        )}
        <button className="btn primary" onClick={runIt} disabled={running || (kind === 'bars' && !inst)}>
          <Icon name="shield" /> {running ? 'Judging… (a few seconds)' : 'Run the gauntlet'}
        </button>
        <p className="faint small">
          Research data ends {lock.data?.research_end ?? '…'}; the lockbox starts {lock.data?.sealed_from ?? '…'}. Every grid
          setting becomes a trial, so the next candidate on this instrument faces a higher bar.
        </p>
        {spent && (
          <div className="note warn">
            The lockbox for <b>{question}</b> is already spent: something looked at data after{' '}
            {lock.data?.research_end}. A forward paper test will have to replace it.
          </div>
        )}
      </div>
    </Card>
  );
}

/** Thresholds tab: the version in force, an editor that needs a reason, and the history. */
function ThresholdsPanel() {
  const th = useApi<ThresholdState>('/gauntlet/thresholds');
  const notify = useToast();
  const [key, setKey] = useState('perm_max_p');
  const [value, setValue] = useState('');
  const [reason, setReason] = useState('');

  async function save() {
    const v = typeof th.data?.config[key] === 'string' ? value : Number(value);
    try {
      const res = await post<{ version: number }>('/gauntlet/thresholds', { changes: { [key]: v }, reason });
      notify(`Thresholds v${res.version} saved. Earlier cards keep the version they were judged with.`, 'ok');
      setReason('');
      await th.reload();
    } catch (err) {
      notify(`Not saved: ${String(err)}`, 'bad');
    }
  }

  const rows = Object.entries(th.data?.config ?? {}).map(([k, v]) => ({ k, v }));
  return (
    <div className="grid cols-2">
      <Card title={`Thresholds in force · v${th.data?.version ?? '…'}`}>
        <DataTable
          rows={rows}
          rowKey={(r) => r.k}
          columns={[
            { key: 'k', label: 'Name', render: (r) => <span className="num">{r.k}</span> },
            { key: 'd', label: 'Meaning', render: (r) => <span className="muted">{THRESHOLD_DOCS[r.k] ?? ''}</span> },
            { key: 'v', label: 'Value', align: 'right', render: (r) => <b className="num">{String(r.v)}</b> },
          ]}
        />
      </Card>
      <div className="stack">
        <Card title="Change a threshold">
          <div className="form-grid">
            <div className="note warn">
              Thresholds are fixed before testing. A change applies to future cards only, needs a reason, and is
              audited. Base changes on calibration, never on a real strategy you want to pass.
            </div>
            <select value={key} onChange={(e) => setKey(e.target.value)}>
              {rows.map((r) => <option key={r.k} value={r.k}>{r.k}</option>)}
            </select>
            <input placeholder={String(th.data?.config[key] ?? '')} value={value} onChange={(e) => setValue(e.target.value)} />
            <input placeholder="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
            <button className="btn" disabled={!value || !reason.trim()} onClick={save}>Save as a new version</button>
          </div>
        </Card>
        <Card title="History">
          <DataTable
            rows={th.data?.history ?? []}
            rowKey={(h) => String(h.version)}
            columns={[
              { key: 'v', label: 'Version', render: (h) => `v${h.version}` },
              { key: 'when', label: 'When', render: (h) => <span className="num">{utc(h.created_at)}</span> },
              { key: 'why', label: 'Reason', render: (h) => h.reason },
            ]}
          />
        </Card>
      </div>
    </div>
  );
}

/**
 * Gauntlet Console: judge candidates, read their report cards, open lockboxes, calibrate the gates and
 * manage thresholds.
 */
export function Gauntlet() {
  const [tab, setTab] = useState<'cards' | 'calibration' | 'thresholds'>('cards');
  const cards = useApi<CardRow[]>('/gauntlet/cards?limit=200');
  const funnel = useApi<FunnelRow[]>('/gauntlet/funnel');
  const [card, setCard] = useState<GauntletCard | null>(null);
  const notify = useToast();

  useEffect(() => {
    if (!card && cards.data?.length) {
      get<GauntletCard>(`/gauntlet/cards/${cards.data[0].card_id}`).then(setCard).catch(() => undefined);
    }
  }, [cards.data, card]);

  async function show(id: string) {
    try {
      setCard(await get<GauntletCard>(`/gauntlet/cards/${id}`));
    } catch (err) {
      notify(`Could not load card: ${String(err)}`, 'bad');
    }
  }

  async function changed(c: GauntletCard) {
    setCard(c);
    await Promise.all([cards.reload(), funnel.reload()]);
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Gauntlet</h1>
        <span className="muted">Ten tests, cheapest first. Most ideas fail; that is the lab working.</span>
      </div>
      <div className="tabs">
        {(['cards', 'calibration', 'thresholds'] as const).map((t) => (
          <button key={t} className={`tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
            {t === 'cards' ? 'Report cards' : t === 'calibration' ? 'Calibration' : 'Thresholds'}
          </button>
        ))}
      </div>

      {tab === 'calibration' && <CalibrationPanel />}
      {tab === 'thresholds' && <ThresholdsPanel />}
      {tab === 'cards' && (
        <div className="studio">
          <div className="stack">
            <RunForm onCard={changed} />
            <Card title="Funnel">
              {funnel.data && funnel.data[0]?.reached ? (
                <EChart option={funnelOption(funnel.data)} height={300} />
              ) : (
                <div className="empty">No cards yet.</div>
              )}
            </Card>
          </div>
          <div className="stack">
            <Card title={`Report cards · ${int(cards.data?.length)}`}>
              <DataTable
                rows={cards.data ?? []}
                rowKey={(c) => c.card_id}
                maxHeight={260}
                onRowClick={(c) => show(c.card_id)}
                empty="No report cards yet. Judge a candidate on the left."
                columns={[
                  { key: 'when', label: 'When', sort: (c) => c.created_at, render: (c) => <span className="num">{utc(c.created_at).slice(5, 16)}</span> },
                  { key: 'question', label: 'Question', sort: (c) => c.question },
                  { key: 'p', label: 'Params', render: (c) => <span className="faint num">{Object.entries(c.params).map(([k, v]) => `${k}=${v}`).join(' ')}</span> },
                  { key: 'v', label: 'Verdict', sort: (c) => c.verdict, render: (c) => <VerdictBadge verdict={c.verdict} /> },
                  {
                    key: 's',
                    label: 'Stages',
                    render: (c) => (
                      <span className="row" style={{ gap: 3 }}>
                        {c.stages.map((s) => (
                          <span key={s.key} title={s.key} className={`dot ${stageTone(s.passed).tone}`} />
                        ))}
                      </span>
                    ),
                  },
                  { key: 'th', label: 'Rules', render: (c) => <Badge tone="muted">v{c.thresholds_version}</Badge> },
                ]}
              />
            </Card>
            {card ? <ReportCard card={card} onChange={changed} /> : <div className="empty">Pick a card to read it.</div>}
          </div>
        </div>
      )}
    </div>
  );
}
