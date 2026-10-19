import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { EChartsOption } from 'echarts';
import {
  post,
  type GauntletCard,
  type MiningBudget,
  type MiningCalibration,
  type MiningCandidate,
  type MiningGrammar,
  type MiningJob,
  type MiningReport,
} from '../api';
import { AXIS, GRID_LINE } from '../charts';
import { Badge, type BadgeTone } from '../components/Badge';
import { Card } from '../components/Card';
import { DataTable } from '../components/DataTable';
import { EChart } from '../components/EChart';
import { Icon } from '../components/Icon';
import { Stat } from '../components/Stat';
import { useToast } from '../components/Toast';
import { num, pct, utc } from '../format';
import { useApi } from '../hooks';

const STATUS_TONE: Record<MiningJob['status'], BadgeTone> = {
  queued: 'info', running: 'info', done: 'ok', stopped: 'warn', failed: 'bad', refused: 'warn',
};
const PARADIGM_TEXT: Record<string, string> = {
  random: 'Random: independent draws from the grammar, the honest baseline.',
  tpe: 'Bayesian (Optuna TPE): draws more often what scored well before.',
  gp: 'Genetic programming: breeds and mutates the best formulas and rules.',
  llm: 'Local model: proposes ideas with an economic rationale, never sees results.',
};
const BANNABLE = ['rule', 'formula', 'trend', 'calm', 'time', 'trail', 'vol_target', 'overnight', 'day', 'long_short',
  'volume', 'dvol', 'cs_rank', 'ts_corr', 'dow', 'tom', 'month'];

/**
 * The race: the best edge found so far against the edge luck alone reaches among N trials, and roughly where the
 * Deflated Sharpe reaches 0.95, as the number of trials on the scope grows.
 *
 * @param r - Job report.
 * @returns ECharts option.
 */
function raceOption(r: MiningReport): EChartsOption {
  const pts = r.curve;
  const line = (name: string, ys: (number | null)[], color: string, dashed = false) => ({
    name,
    type: 'line' as const,
    showSymbol: false,
    step: name === 'Best found' ? ('end' as const) : undefined,
    data: pts.map((p, i) => [p.N, ys[i]]),
    lineStyle: { width: dashed ? 1.2 : 1.8, color, type: dashed ? ('dashed' as const) : ('solid' as const) },
    itemStyle: { color },
  });
  return {
    animation: false,
    tooltip: { trigger: 'axis', valueFormatter: (v) => num(v as number) },
    legend: { data: ['Best found', 'Luck reaches', 'DSR 0.95 needs about'], textStyle: { color: AXIS }, top: 0 },
    grid: { left: 48, right: 16, top: 30, bottom: 36 },
    xAxis: { type: 'value', name: 'trials on the scope (N)', nameLocation: 'middle', nameGap: 24, min: 'dataMin',
      nameTextStyle: { color: AXIS }, axisLabel: { color: AXIS }, splitLine: { show: false } },
    yAxis: { type: 'value', name: 'edge Sharpe', nameTextStyle: { color: AXIS }, axisLabel: { color: AXIS },
      splitLine: { lineStyle: { color: GRID_LINE } } },
    series: [
      line('Best found', pts.map((p) => p.best), '#38bdf8'),
      line('Luck reaches', pts.map((p) => p.sr0), AXIS, true),
      line('DSR 0.95 needs about', pts.map((p) => p.bar), '#fbbf24', true),
    ],
  };
}

/**
 * Every scored trial's edge Sharpe as a histogram, with the spread luck alone would give one trial drawn over it.
 *
 * @param h - The report's histogram.
 * @returns ECharts option.
 */
function histogramOption(h: NonNullable<MiningReport['histogram']>): EChartsOption {
  const width = h.bins[1] - h.bins[0];
  const mids = h.counts.map((_, i) => h.bins[i] + width / 2);
  const luck = mids.map((x) => (h.n * width * Math.exp(-0.5 * (x / h.luck_sd) ** 2)) / (h.luck_sd * Math.sqrt(2 * Math.PI)));
  return {
    animation: false,
    tooltip: { trigger: 'axis' },
    legend: { data: ['Trials', 'Luck, one trial'], textStyle: { color: AXIS }, top: 0 },
    grid: { left: 44, right: 12, top: 30, bottom: 24 },
    xAxis: { type: 'value', min: -2, max: 2, axisLabel: { color: AXIS }, splitLine: { show: false } },
    yAxis: { type: 'value', axisLabel: { color: AXIS }, splitLine: { lineStyle: { color: GRID_LINE } } },
    series: [
      { name: 'Trials', type: 'bar', barWidth: '90%', data: mids.map((x, i) => [x, h.counts[i]]), itemStyle: { color: '#38bdf8' } },
      { name: 'Luck, one trial', type: 'line', showSymbol: false, data: mids.map((x, i) => [x, luck[i]]),
        lineStyle: { color: '#fbbf24', width: 1.4 }, itemStyle: { color: '#fbbf24' } },
    ],
  };
}

/**
 * Coverage of the search space: how often each kind of signal met each component, coloured by count, with the best
 * edge in the tooltip.
 *
 * @param c - The report's cells.
 * @returns ECharts option.
 */
function coverageOption(c: NonNullable<MiningReport['cells']>): EChartsOption {
  const data = c.values.map(([count, best], k) => [k % c.cols.length, Math.floor(k / c.cols.length), count, best]);
  const most = Math.max(1, ...c.values.map((v) => v[0]));
  return {
    animation: false,
    tooltip: {
      formatter: (p: unknown) => {
        const v = (p as { value: [number, number, number, number | null] }).value;
        return `${c.rows[v[1]]} × ${c.cols[v[0]]}<br/>${v[2]} trials${v[3] !== null ? `, best edge ${num(v[3])}` : ''}`;
      },
    },
    grid: { left: 150, right: 8, top: 4, bottom: 70 },
    xAxis: { type: 'category', data: c.cols, axisLabel: { color: AXIS, rotate: 40, fontSize: 10 } },
    yAxis: { type: 'category', data: c.rows, axisLabel: { color: AXIS, fontSize: 10 } },
    visualMap: { min: 0, max: most, show: false, inRange: { color: ['#141a24', '#0e3a52', '#38bdf8'] } },
    series: [{ type: 'heatmap', data, label: { show: true, color: '#cbd5e1', fontSize: 9, formatter: (p: unknown) => String((p as { value: number[] }).value[2] || '') },
      itemStyle: { borderColor: '#0b0f14', borderWidth: 1 } }],
  };
}

/**
 * Horizontal bars of counts, e.g. how often each operator was used.
 *
 * @param counts - Name to count.
 * @param color - Bar colour.
 * @returns ECharts option.
 */
function countsOption(counts: Record<string, number>, color: string): EChartsOption {
  const items = Object.entries(counts).sort((a, b) => a[1] - b[1]).slice(-16);
  return {
    animation: false,
    tooltip: { trigger: 'axis' },
    grid: { left: 90, right: 16, top: 4, bottom: 20 },
    xAxis: { type: 'value', axisLabel: { color: AXIS }, splitLine: { lineStyle: { color: GRID_LINE } } },
    yAxis: { type: 'category', data: items.map((i) => i[0]), axisLabel: { color: AXIS, fontSize: 10 } },
    series: [{ type: 'bar', data: items.map((i) => i[1]), itemStyle: { color } }],
  };
}

/**
 * Tone of a Deflated Sharpe: green past 0.95, amber past 0.8, red below.
 *
 * @param d - DSR.
 * @returns Badge tone.
 */
function dsrTone(d: number | null | undefined): BadgeTone {
  if (d === null || d === undefined) return 'muted';
  return d >= 0.95 ? 'ok' : d >= 0.8 ? 'warn' : 'bad';
}

/**
 * One candidate's details: the rule in words, its formula, parameters, the model's rationale and the gauntlet button.
 *
 * @param props.c - Candidate.
 * @param props.busy - Whether a gauntlet run is in flight.
 * @param props.onJudge - Sends the candidate to the gauntlet.
 */
function CandidateDetail({ c, busy, onJudge }: { c: MiningCandidate; busy: boolean; onJudge: () => void }) {
  return (
    <div className="note">
      <div style={{ marginBottom: 6 }}><b>{c.strategy}</b> · {c.text}</div>
      {c.formula && <div className="num small">formula: {c.formula}</div>}
      <div className="small">
        parameters {Object.entries(c.params).map(([k, v]) => `${k} ${v}`).join(', ') || 'none'} · exposure {pct(c.exposure, 0)} ·
        CAGR {pct(c.cagr, 1, true)} · max drawdown {pct(c.max_dd ? -c.max_dd : null)} · luck threshold at N = {c.n_trials}:{' '}
        {num(c.sr0)}
      </div>
      {c.rationale && <div className="small" style={{ marginTop: 6 }}>Why (the model's rationale): {c.rationale}</div>}
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn" onClick={onJudge} disabled={busy}>
          <Icon name="shield" /> {busy ? 'Judging…' : 'Send to the gauntlet'}
        </button>
        <span className="faint small">Judged like any card: N counts every trial on this scope, this job's included.</span>
      </div>
    </div>
  );
}

/**
 * Mining Lab: start budgeted searches over the grammar, watch the best find race the luck threshold, read what the
 * search covered, and send candidates to the gauntlet. Every candidate is a ledger trial, so the bar rises with
 * every try.
 */
export function MiningLab() {
  const navigate = useNavigate();
  const notify = useToast();
  const grammar = useApi<MiningGrammar>('/mining/grammar');
  const jobs = useApi<MiningJob[]>('/mining/jobs', 4000);
  const budgets = useApi<MiningBudget[]>('/mining/budgets', 4000);
  const calib = useApi<MiningCalibration | null>('/mining/calibration');
  const [selected, setSelected] = useState<string | null>(null);
  const jobId = selected ?? jobs.data?.[0]?.job_id ?? null;
  const live = jobs.data?.some((j) => j.job_id === jobId && (j.status === 'running' || j.status === 'queued'));
  const report = useApi<MiningReport>(jobId ? `/mining/jobs/${jobId}` : null, live ? 2000 : 0);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState('');

  const [kind, setKind] = useState<'bars' | 'panel'>('panel');
  const [name, setName] = useState('crypto_top30');
  const [paradigm, setParadigm] = useState('gp');
  const [trials, setTrials] = useState(500);
  const [minutes, setMinutes] = useState(30);
  const [workers, setWorkers] = useState(6);
  const [seed, setSeed] = useState(1);
  const [banned, setBanned] = useState<string[]>([]);
  const [hint, setHint] = useState('');
  const [note, setNote] = useState('');
  const [budgetEdit, setBudgetEdit] = useState({ question: '', max_trials: 2000, reason: '' });

  const instruments = useMemo(() => (grammar.data?.instruments ?? []).map(([, s]) => s), [grammar.data]);
  const question = `mined:${name}:1d`;
  const budget = budgets.data?.find((b) => b.question === question);
  const remaining = budget ? budget.remaining : grammar.data?.default_budget;

  useEffect(() => {
    const universes = grammar.data?.universes ?? [];
    if (kind === 'bars' && universes.some((u) => u.name === name)) setName('SPY');
    if (kind === 'panel' && !universes.some((u) => u.name === name)) setName('crypto_top30');
  }, [kind, name, grammar.data]);

  useEffect(() => setOpen(null), [jobId]);

  async function start() {
    setBusy('start');
    try {
      const j = await post<MiningJob>('/mining/jobs', { kind, name, paradigm, max_trials: trials, max_minutes: minutes,
        workers, seed, banned, hint, note });
      notify(`Job ${j.job_id} queued on ${j.question}.`, 'ok');
      setSelected(j.job_id);
      await jobs.reload();
    } catch (err) {
      notify(`Could not start: ${String(err)}`, 'bad');
    } finally {
      setBusy('');
    }
  }

  async function stop(id: string) {
    try {
      await post(`/mining/jobs/${id}/stop`);
      notify(`Job ${id} stops after its current batch.`, 'ok');
      await jobs.reload();
    } catch (err) {
      notify(`Could not stop: ${String(err)}`, 'bad');
    }
  }

  async function judge(c: MiningCandidate) {
    if (!jobId) return;
    setBusy(c.strategy);
    try {
      const card = await post<GauntletCard>(`/mining/jobs/${jobId}/gauntlet`, { strategy: c.strategy });
      notify(`${card.meta.question}: ${card.verdict}${card.failed_stage ? ` at ${card.failed_stage}` : ''} (card ${card.card_id}).`,
        card.verdict === 'rejected' ? 'warn' : 'ok');
      await report.reload();
    } catch (err) {
      notify(`Gauntlet failed: ${String(err)}`, 'bad');
    } finally {
      setBusy('');
    }
  }

  async function saveBudget() {
    try {
      const b = await post<MiningBudget>('/mining/budgets', budgetEdit);
      notify(`${b.question}: budget ${b.max_trials} trials (${b.used} used).`, 'ok');
      await budgets.reload();
    } catch (err) {
      notify(`Budget not changed: ${String(err)}`, 'bad');
    }
  }

  const r = report.data;
  const p = r?.progress;
  const worst = calib.data?.noise.length ? calib.data.noise.reduce((a, b) => (b.rate > a.rate ? b : a)) : null;
  const mined = (jobs.data ?? []).reduce((s, j) => s + (j.n_trials ?? 0), 0);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Mining Lab</h1>
        <span className="muted">
          Budgeted searches over a grammar of signals, filters, exits and sizing. Every candidate is a ledger trial, so
          the bar luck sets rises with every try.
        </span>
      </div>
      <div className="stats-row">
        <Stat label="Noise at mining scale" term="mining_calibration"
          value={worst ? pct(worst.rate, 1) : '–'} tone={worst ? (worst.rate > 0.05 ? 'down' : 'up') : ''}
          sub={worst ? `worst family (${worst.family}); target ≤ 5%` : 'no mining calibration yet'} />
        <Stat label="Pooled" value={calib.data ? pct(calib.data.fpr_overall, 2) : '–'}
          sub={calib.data ? `${calib.data.settings.n_runs} runs, study ${calib.data.id}` : 'qlab calibrate-mining'} />
        <Stat label="Jobs" value={jobs.data?.length ?? 0} sub={`${(jobs.data ?? []).filter((j) => j.status === 'running').length} running`} />
        <Stat label="Mined trials" term="n_trials" value={mined.toLocaleString()} sub="every one in the ledger" />
      </div>

      <div className="studio">
        <div className="stack">
          <Card title="Start a job" term="mining">
            <div className="form-grid">
              <label className="field">
                Search
                <select value={kind} onChange={(e) => setKind(e.target.value as 'bars' | 'panel')}>
                  <option value="panel">a universe (breadth: more power)</option>
                  <option value="bars">one instrument</option>
                </select>
              </label>
              <label className="field">
                {kind === 'panel' ? 'Universe' : 'Instrument'}
                {kind === 'panel' ? (
                  <select value={name} onChange={(e) => setName(e.target.value)}>
                    {(grammar.data?.universes ?? []).map((u) => <option key={u.name} value={u.name}>{u.name}</option>)}
                  </select>
                ) : (
                  <>
                    <input list="mining-instruments" value={name} onChange={(e) => setName(e.target.value.toUpperCase())} />
                    <datalist id="mining-instruments">{instruments.map((s) => <option key={s} value={s} />)}</datalist>
                  </>
                )}
              </label>
              <label className="field">
                Paradigm
                <select value={paradigm} onChange={(e) => setParadigm(e.target.value)}>
                  {(grammar.data?.paradigms ?? ['random', 'tpe', 'gp', 'llm']).map((x) => <option key={x} value={x}>{x}</option>)}
                </select>
              </label>
              <p className="muted small">{PARADIGM_TEXT[paradigm]}</p>
              <div className="row">
                <label className="field" style={{ flex: 1 }}>Trials
                  <input type="number" min={1} value={trials} onChange={(e) => setTrials(Number(e.target.value))} />
                </label>
                <label className="field" style={{ flex: 1 }}>Minutes
                  <input type="number" min={1} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} />
                </label>
              </div>
              <div className="row">
                <label className="field" style={{ flex: 1 }}>Workers
                  <input type="number" min={1} max={14} value={workers} onChange={(e) => setWorkers(Number(e.target.value))} />
                </label>
                <label className="field" style={{ flex: 1 }}>Seed
                  <input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
                </label>
              </div>
              <p className="faint small">
                {question}: {remaining !== undefined ? `${remaining.toLocaleString()} trials left in its budget` : ''}.
                Each trial raises N for every later candidate on {name}.
              </p>
              <details>
                <summary className="small muted">Leave out parts of the grammar ({banned.length})</summary>
                <div className="chips" style={{ marginTop: 6 }}>
                  {BANNABLE.map((b) => (
                    <label key={b} className="row small" style={{ gap: 4 }}>
                      <input type="checkbox" checked={banned.includes(b)}
                        onChange={(e) => setBanned(e.target.checked ? [...banned, b] : banned.filter((x) => x !== b))} />
                      {b}
                    </label>
                  ))}
                </div>
              </details>
              {paradigm === 'llm' && (
                <label className="field">Research direction for the model
                  <input value={hint} placeholder="e.g. liquidity and volume effects" onChange={(e) => setHint(e.target.value)} />
                </label>
              )}
              <label className="field">Why this job
                <input value={note} onChange={(e) => setNote(e.target.value)} />
              </label>
              <button className="btn primary" onClick={start} disabled={!!busy || !name}>
                <Icon name="play" /> {busy === 'start' ? 'Starting…' : 'Start mining'}
              </button>
            </div>
          </Card>

          <Card title="Jobs">
            <DataTable
              rows={jobs.data ?? []}
              rowKey={(j) => j.job_id}
              maxHeight={360}
              onRowClick={(j) => setSelected(j.job_id)}
              empty="No mining jobs yet."
              columns={[
                { key: 'q', label: 'Question', sort: (j) => j.question, render: (j) => (
                  <span title={j.job_id} className={j.job_id === jobId ? 'warn' : ''}>{j.scope} · {j.paradigm}</span>) },
                { key: 'status', label: 'Status', render: (j) => <Badge tone={STATUS_TONE[j.status]}>{j.status}</Badge> },
                { key: 'n', label: 'Trials', align: 'right', sort: (j) => j.n_trials, render: (j) => `${j.n_trials ?? 0}/${j.settings.max_trials}` },
                { key: 'best', label: 'Best', align: 'right', sort: (j) => j.best_edge, render: (j) => num(j.best_edge) },
                { key: 'x', label: '', render: (j) => (j.status === 'running' || j.status === 'queued') && j.live ? (
                  <button className="btn small" onClick={(e) => { e.stopPropagation(); void stop(j.job_id); }}>Stop</button>) : null },
              ]}
            />
          </Card>

          <Card title="Budgets per question" term="mining_budget">
            <DataTable
              rows={budgets.data ?? []}
              rowKey={(b) => b.question}
              empty="No mining question yet."
              onRowClick={(b) => setBudgetEdit({ question: b.question, max_trials: b.max_trials, reason: '' })}
              columns={[
                { key: 'q', label: 'Question', render: (b) => b.question.replace(/^mined:|:1d$/g, '') },
                { key: 'u', label: 'Used', align: 'right', render: (b) => `${b.used.toLocaleString()} / ${b.max_trials.toLocaleString()}` },
              ]}
            />
            <div className="form-grid" style={{ marginTop: 10 }}>
              <label className="field">Question
                <input value={budgetEdit.question} placeholder="mined:SPY:1d" onChange={(e) => setBudgetEdit({ ...budgetEdit, question: e.target.value })} />
              </label>
              <div className="row">
                <label className="field" style={{ flex: 1 }}>Cap
                  <input type="number" min={0} value={budgetEdit.max_trials} onChange={(e) => setBudgetEdit({ ...budgetEdit, max_trials: Number(e.target.value) })} />
                </label>
                <label className="field" style={{ flex: 2 }}>Reason
                  <input value={budgetEdit.reason} onChange={(e) => setBudgetEdit({ ...budgetEdit, reason: e.target.value })} />
                </label>
              </div>
              <button className="btn" onClick={saveBudget} disabled={!budgetEdit.question || !budgetEdit.reason.trim()}>Set budget</button>
            </div>
          </Card>
        </div>

        <div className="stack">
          {!r && <div className="empty">Start a job, or pick one from the board.</div>}
          {r && p && (
            <>
              <div className={`note ${r.status === 'failed' || r.status === 'refused' ? 'warn' : ''}`}>
                Job <b className="num">{r.job_id}</b> · <b>{r.question}</b> · {r.paradigm} · <Badge tone={STATUS_TONE[r.status]}>{r.status}</Badge>{' '}
                {r.research && <>· research {r.research[0]} → {r.research[1]} (the lockbox stays sealed) </>}
                · {r.prior_n ?? 0} trials were already on {r.scope.name} · started {utc(r.started_at ?? r.created_at).slice(0, 16)}
                {r.message && <div style={{ marginTop: 4 }}>{r.message}</div>}
              </div>
              <div className="stats-row">
                <Stat label="Trials" value={`${p.n_trials}`} sub={`${p.n_valid ?? 0} scored · ${p.leaks ?? 0} leaks · ${num(p.elapsed, 0)} s`} />
                <Stat label="Best edge found" term="edge_sharpe" value={num(p.best_edge)} sub="in sample, after costs" />
                <Stat label="Luck reaches" term="dsr" value={num(p.sr0)} sub={`best of N = ${p.N ?? 0} trials with no edge`} />
                <Stat label="DSR 0.95 needs" value={num(p.bar)} tone={p.best_edge !== null && p.best_edge !== undefined && p.bar !== undefined && p.best_edge >= p.bar ? 'up' : ''}
                  sub="about; the gauntlet decides" />
                <Stat label="Best DSR" value={r.top.length ? num(r.top[0].dsr, 3) : '–'} tone={r.top.length && r.top[0].dsr >= 0.95 ? 'up' : 'down'}
                  sub={`variance: ${r.dsr_variance ?? '–'} (thresholds v${r.thresholds_version ?? '–'})`} />
              </div>
              <Card title="Best so far against the luck threshold" term="mining_race">
                {r.curve.length ? <EChart option={raceOption(r)} height={280} /> : <div className="empty">Waiting for the first batch.</div>}
                <p className="faint small">
                  Each trial raises N, and the edge luck alone can produce among N trials rises with it. A find is worth the
                  gauntlet's time only when it outruns the amber line, and even then the gauntlet's other stages decide.
                </p>
              </Card>
              <Card title="Candidates" term="originality">
                <DataTable
                  rows={r.top}
                  rowKey={(c) => c.strategy + JSON.stringify(c.params)}
                  onRowClick={(c) => setOpen(open === c.strategy ? null : c.strategy)}
                  empty="No scored candidates yet (they appear when the job ends)."
                  columns={[
                    { key: 'rule', label: 'Rule', render: (c) => <span title={c.text}>{c.text.length > 70 ? `${c.text.slice(0, 70)}…` : c.text}</span> },
                    { key: 'edge', label: 'Edge', align: 'right', sort: (c) => c.edge, render: (c) => num(c.edge) },
                    { key: 'gross', label: 'Gross', align: 'right', sort: (c) => c.gross_edge, render: (c) => num(c.gross_edge) },
                    { key: 'dsr', label: 'DSR', align: 'right', sort: (c) => c.dsr, render: (c) => <Badge tone={dsrTone(c.dsr)}>{num(c.dsr, 3)}</Badge> },
                    { key: 'to', label: 'Turnover', align: 'right', sort: (c) => c.turnover, render: (c) => `${num(c.turnover, 1)}/y` },
                    { key: 'orig', label: 'Like a known rule', align: 'right', sort: (c) => Math.abs(c.originality.corr ?? 0),
                      render: (c) => c.originality.rule ? `${num(c.originality.corr)} ${c.originality.rule}` : '–' },
                  ]}
                />
                {r.top.filter((c) => c.strategy === open).map((c) => (
                  <CandidateDetail key={c.strategy} c={c} busy={busy === c.strategy} onJudge={() => void judge(c)} />
                ))}
                {r.cards && r.cards.length > 0 && (
                  <div className="row small" style={{ marginTop: 8 }}>
                    Sent to the gauntlet:
                    {r.cards.map((c) => (
                      <button key={c.card_id} className="btn small" onClick={() => navigate('/gauntlet')}>
                        {c.card_id} <Badge tone={c.verdict === 'rejected' ? 'bad' : 'ok'}>{c.verdict}{c.failed_stage ? ` at ${c.failed_stage}` : ''}</Badge>
                      </button>
                    ))}
                  </div>
                )}
              </Card>
              {r.reference && r.reference.length > 0 && (
                <Card title="Known rules at their published defaults (the baseline)">
                  <div className="row small">
                    {r.reference.map((x) => (
                      <span key={x.rule} className="note">{x.rule}: edge <b className="num">{num(x.edge)}</b>, turnover {num(x.turnover, 1)}/y</span>
                    ))}
                  </div>
                </Card>
              )}
              <div className="grid cols-2">
                <Card title="Search-space coverage" term="search_coverage">
                  {r.cells && r.cells.rows.length ? <EChart option={coverageOption(r.cells)} height={Math.max(220, r.cells.rows.length * 22 + 90)} />
                    : <div className="empty">Appears when the job ends.</div>}
                </Card>
                <Card title="Every trial's edge against luck">
                  {r.histogram ? <EChart option={histogramOption(r.histogram)} height={260} /> : <div className="empty">Appears when the job ends.</div>}
                  {r.histogram && <p className="faint small">{r.histogram.below_zero} of {r.histogram.n} scored trials lost to the passive alternative after costs.</p>}
                </Card>
              </div>
              {r.coverage && (
                <div className="grid cols-2">
                  <Card title="Operators used">
                    <EChart option={countsOption(r.coverage.ops ?? {}, '#a78bfa')} height={260} />
                  </Card>
                  <Card title="Data the formulas read">
                    <EChart option={countsOption(r.coverage.terminals ?? {}, '#34d399')} height={260} />
                  </Card>
                </div>
              )}
              {(r.outside?.length || r.proposals?.length) ? (
                <Card title="The model's ideas" term="mining_rationale">
                  {r.llm && <p className="faint small">{r.llm.calls} calls to {r.llm.model}; {r.llm.rejected} proposals rejected by the grammar.</p>}
                  {(r.proposals ?? []).slice(0, 20).map((x, i) => (
                    <p key={i} className="small"><b>{x.text}</b><br /><span className="muted">{x.rationale}</span></p>
                  ))}
                  {r.outside && r.outside.length > 0 && (
                    <>
                      <p className="small muted">Outside the grammar (not run; ideas for new data or components):</p>
                      <ul className="small">{r.outside.map((x, i) => <li key={i}>{x}</li>)}</ul>
                    </>
                  )}
                </Card>
              ) : null}
            </>
          )}

          {calib.data && (
            <Card title="Calibration at mining scale" term="mining_calibration">
              <p className="lead" style={{ marginBottom: 10 }}>{calib.data.headline}</p>
              <DataTable
                rows={calib.data.benchmark}
                rowKey={(b) => `${b.family}:${b.scenario}:${b.true_edge}`}
                maxHeight={420}
                columns={[
                  { key: 'family', label: 'Miner', sort: (b) => b.family, render: (b) => b.family.replace('mine/', '') },
                  { key: 'market', label: 'Market', render: (b) => b.scenario === 'noise_mined' ? 'noise' : `edge ${num(b.true_edge)}` },
                  { key: 'rate', label: 'Admitted', align: 'right', sort: (b) => b.rate, render: (b) => (
                    <span className={b.scenario === 'noise_mined' && b.rate > 0.05 ? 'warn' : ''}>{b.admitted}/{b.n} ({pct(b.rate, 0)})</span>) },
                  { key: 'best', label: 'Best in sample', align: 'right', render: (b) => num(b.best_edge) },
                  { key: 'sr0', label: 'Luck reached', align: 'right', render: (b) => num(b.sr0) },
                  { key: 'f', label: 'Formulas', align: 'right', render: (b) => pct(b.formula_share, 0) },
                ]}
              />
              <p className="faint small">
                Miners search synthetic markets with no edge (noise) or a planted one, then submit their best to the
                unchanged gauntlet, deflated against every trial they made. Noise must pass at most 5% of the time in every
                family. Thresholds change only from studies like this one.
              </p>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
