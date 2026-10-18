import { useState, type ReactNode } from 'react';
import type { EChartsOption } from 'echarts';
import { post, type GauntletCard, type StageResult } from '../api';
import { int, num, pct } from '../format';
import { Badge, type BadgeTone } from './Badge';
import { Card } from './Card';
import { EChart } from './EChart';
import { Stat } from './Stat';
import { useToast } from './Toast';

const AXIS = '#8a97a8';
const GRID_LINE = '#151c26';
const ACCENT = '#38bdf8';
const GREY = '#5c6878';

/** Glossary key for each stage's info tooltip. */
const TERMS: Record<string, string> = {
  leakage: 'lookahead',
  costs: 'breakeven',
  walk_forward: 'walk_forward',
  plateau: 'plateau',
  permutation: 'permutation',
  bootstrap: 'bootstrap',
  significance: 'dsr',
  regimes: 'regime',
  diversification: 'edge_sharpe',
  lockbox: 'lockbox',
};

/**
 * Badge tone and label for a stage outcome.
 *
 * @param passed - Stage outcome.
 * @returns Tone and short label.
 */
export function stageTone(passed: boolean | null): { tone: BadgeTone; label: string } {
  if (passed === true) return { tone: 'ok', label: 'pass' };
  if (passed === false) return { tone: 'bad', label: 'fail' };
  return { tone: 'muted', label: 'info' };
}

/**
 * Badge for a card verdict.
 *
 * @param props.verdict - `candidate`, `rejected` or `validated`.
 */
export function VerdictBadge({ verdict }: { verdict: string }) {
  const tone: BadgeTone = verdict === 'validated' ? 'ok' : verdict === 'candidate' ? 'info' : 'bad';
  return <Badge tone={tone}>{verdict}</Badge>;
}

/**
 * Equal-width histogram.
 *
 * @param values - Samples.
 * @param bins - Number of bins.
 * @returns Bin centres and counts.
 */
function histogram(values: number[], bins = 30): { centers: number[]; counts: number[] } {
  const finite = values.filter((v) => Number.isFinite(v));
  if (!finite.length) return { centers: [], counts: [] };
  const lo = Math.min(...finite);
  const hi = Math.max(...finite);
  const width = (hi - lo) / bins || 1;
  const counts = new Array(bins).fill(0);
  for (const v of finite) counts[Math.min(bins - 1, Math.floor((v - lo) / width))] += 1;
  return { centers: counts.map((_, i) => lo + (i + 0.5) * width), counts };
}

/**
 * Histogram with vertical marker lines, e.g. the null distribution with the real result on top.
 *
 * @param values - Samples.
 * @param marks - Lines to draw: label, x value and colour.
 * @returns ECharts option.
 */
function histogramOption(
  values: number[],
  marks: { name: string; x: number | null; color: string; label?: 'end' | 'insideEndTop' | 'insideStartTop' }[],
): EChartsOption {
  const h = histogram(values);
  return {
    animation: false,
    grid: { left: 40, right: 12, top: 22, bottom: 26 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'value', axisLabel: { color: AXIS }, splitLine: { show: false } },
    yAxis: { type: 'value', axisLabel: { color: AXIS }, splitLine: { lineStyle: { color: GRID_LINE } } },
    series: [
      {
        type: 'bar',
        name: 'count',
        barWidth: '90%',
        data: h.centers.map((c, i) => [c, h.counts[i]]),
        itemStyle: { color: GREY },
        markLine: {
          symbol: 'none',
          label: { color: AXIS, formatter: '{b}' },
          data: marks
            .filter((m) => m.x !== null && Number.isFinite(m.x))
            .map((m) => ({
              name: m.name,
              xAxis: m.x as number,
              lineStyle: { color: m.color, width: 2, type: 'solid' },
              label: { position: m.label ?? 'end', color: m.color },
            })),
        },
      },
    ],
  };
}

/** Walk-forward folds: in-sample vs out-of-sample edge Sharpe per test year. */
function foldsOption(chart: Record<string, any>): EChartsOption {
  const folds = (chart.folds ?? []) as { start: string; is_sharpe: number; oos_sharpe: number }[];
  return {
    animation: false,
    legend: { data: ['In-sample', 'Out-of-sample'], textStyle: { color: AXIS }, top: 0 },
    tooltip: { trigger: 'axis' },
    grid: { left: 40, right: 12, top: 26, bottom: 26 },
    xAxis: { type: 'category', data: folds.map((f) => f.start.slice(0, 4)), axisLabel: { color: AXIS } },
    yAxis: { type: 'value', axisLabel: { color: AXIS }, splitLine: { lineStyle: { color: GRID_LINE } } },
    series: [
      { name: 'In-sample', type: 'bar', data: folds.map((f) => f.is_sharpe), itemStyle: { color: GREY } },
      {
        name: 'Out-of-sample',
        type: 'bar',
        itemStyle: { color: '#34d399' },
        data: folds.map((f) => ({ value: f.oos_sharpe, itemStyle: { color: f.oos_sharpe >= 0 ? '#34d399' : '#f87171' } })),
      },
    ],
  };
}

/** Plateau heatmap over the first two parameters, with the candidate outlined. */
function plateauOption(chart: Record<string, any>): EChartsOption {
  const cells = (chart.cells ?? []) as [number, number, number | null][];
  const xs = [...new Set(cells.map((c) => c[0]))].sort((a, b) => a - b);
  const ys = [...new Set(cells.map((c) => c[1]))].sort((a, b) => a - b);
  const vals = cells.map((c) => c[2]).filter((v): v is number => v !== null);
  const span = Math.max(0.1, ...vals.map((v) => Math.abs(v)));
  const cand = chart.candidate as [number, number];
  return {
    animation: false,
    tooltip: {
      formatter: (p: unknown) => {
        const v = (p as { value: number[] }).value;
        return `${chart.x} ${xs[v[0]]}${chart.y ? ` · ${chart.y} ${ys[v[1]]}` : ''}<br/>edge Sharpe ${num(v[2])}`;
      },
    },
    grid: { left: 56, right: 12, top: 8, bottom: 40 },
    xAxis: { type: 'category', name: chart.x, nameLocation: 'middle', nameGap: 24, data: xs.map(String), axisLabel: { color: AXIS } },
    yAxis: { type: 'category', name: chart.y ?? '', data: ys.map(String), axisLabel: { color: AXIS } },
    visualMap: { min: -span, max: span, show: false, inRange: { color: ['#b91c1c', '#3b1d1d', '#141a24', '#0f3b2e', '#059669'] } },
    series: [
      {
        type: 'heatmap',
        data: cells.map((c) => {
          const isCand = c[0] === cand[0] && c[1] === cand[1];
          return {
            value: [xs.indexOf(c[0]), ys.indexOf(c[1]), c[2]],
            itemStyle: isCand ? { borderColor: '#ffffff', borderWidth: 2 } : { borderColor: '#0b0f14', borderWidth: 1 },
          };
        }),
      },
    ],
  };
}

/** Bootstrap fan: percentile bands of resampled equity paths. */
function fanOption(chart: Record<string, any>): EChartsOption {
  const fan = chart.fan as Record<string, number[]>;
  const x = fan.step;
  const band = (lo: string, hi: string, color: string) => [
    { type: 'line' as const, data: fan[lo].map((v, i) => [x[i], v]), stack: lo + hi, lineStyle: { opacity: 0 }, showSymbol: false },
    {
      type: 'line' as const,
      data: fan[hi].map((v, i) => [x[i], v - fan[lo][i]]),
      stack: lo + hi,
      lineStyle: { opacity: 0 },
      showSymbol: false,
      areaStyle: { color },
    },
  ];
  return {
    animation: false,
    grid: { left: 44, right: 12, top: 10, bottom: 26 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'value', name: 'days', axisLabel: { color: AXIS }, splitLine: { show: false } },
    yAxis: { type: 'log', axisLabel: { color: AXIS, formatter: (v: number) => `${v.toFixed(1)}x` }, splitLine: { lineStyle: { color: GRID_LINE } } },
    series: [
      ...band('p5', 'p95', 'rgba(56,189,248,0.12)'),
      ...band('p25', 'p75', 'rgba(56,189,248,0.22)'),
      { type: 'line', name: 'median', data: fan.p50.map((v, i) => [x[i], v]), showSymbol: false, lineStyle: { color: ACCENT, width: 1.6 } },
    ],
  };
}

/** Regimes: average yearly net return of the strategy vs buy-and-hold in each market state. */
function regimesOption(chart: Record<string, any>): EChartsOption {
  const rows = (chart.rows ?? []) as { regime: string; strategy: number; benchmark: number }[];
  return {
    animation: false,
    legend: { data: ['Strategy', 'Buy & hold'], textStyle: { color: AXIS }, top: 0 },
    tooltip: { trigger: 'axis', valueFormatter: (v) => pct(v as number) },
    grid: { left: 48, right: 12, top: 26, bottom: 26 },
    xAxis: { type: 'category', data: rows.map((r) => r.regime), axisLabel: { color: AXIS } },
    yAxis: { type: 'value', axisLabel: { color: AXIS, formatter: (v: number) => `${Math.round(v * 100)}%` }, splitLine: { lineStyle: { color: GRID_LINE } } },
    series: [
      { name: 'Strategy', type: 'bar', data: rows.map((r) => r.strategy), itemStyle: { color: ACCENT } },
      { name: 'Buy & hold', type: 'bar', data: rows.map((r) => r.benchmark), itemStyle: { color: GREY } },
    ],
  };
}

/**
 * The lockbox opener: typed confirmation of the research question and a reason, because it can be done once.
 *
 * @param props.card - Candidate card.
 * @param props.onOpened - Receives the updated card.
 */
function LockboxForm({ card, onOpened }: { card: GauntletCard; onOpened: (c: GauntletCard) => void }) {
  const [confirm, setConfirm] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const notify = useToast();

  async function open() {
    setBusy(true);
    try {
      const updated = await post<GauntletCard>(`/gauntlet/cards/${card.card_id}/lockbox`, { confirm, reason });
      notify(`Lockbox opened: ${updated.verdict}.`, updated.verdict === 'validated' ? 'ok' : 'bad');
      onOpened(updated);
    } catch (err) {
      notify(`Lockbox refused: ${String(err)}`, 'bad');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="form-grid" style={{ marginTop: 10 }}>
      <div className="note warn">
        One use per research question, ever. Type <b className="num">{card.meta.question}</b> to confirm.
      </div>
      <input placeholder={card.meta.question} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      <input placeholder="Why now?" value={reason} onChange={(e) => setReason(e.target.value)} />
      <button className="btn danger" disabled={busy || confirm !== card.meta.question || !reason.trim()} onClick={open}>
        {busy ? 'Opening…' : 'Open the lockbox'}
      </button>
    </div>
  );
}

/**
 * Body of one stage panel: summary, rule, and the stage's own chart or numbers.
 *
 * @param props.stage - Stage result.
 * @param props.card - The whole card, for context the stage needs.
 * @param props.onChange - Receives an updated card (lockbox).
 */
function StageBody({ stage, card, onChange }: { stage: StageResult; card: GauntletCard; onChange: (c: GauntletCard) => void }) {
  const c = stage.chart ?? {};
  let extra: ReactNode = null;
  switch (stage.key) {
    case 'costs':
      extra = (
        <div className="stats-row">
          <Stat label="Edge before costs" value={num(c.gross_edge_sharpe)} />
          <Stat label="Edge after costs" term="edge_sharpe" value={num(c.net_edge_sharpe)} />
          <Stat label="Break-even" term="breakeven" value={`${num(c.breakeven_bps, 0)} bps`} sub={`charged ${num(c.cost_bps, 1)} bps`} />
          <Stat label="Beta" value={num(c.beta)} sub={`turnover ${num(c.turnover, 1)}/yr`} />
        </div>
      );
      break;
    case 'walk_forward':
      extra = <EChart option={foldsOption(c)} height={200} />;
      break;
    case 'plateau':
      extra = c.cells ? <EChart option={plateauOption(c)} height={230} /> : null;
      break;
    case 'permutation':
      extra = (
        <EChart
          option={histogramOption(c.null ?? [], [{ name: 'real', x: c.real, color: ACCENT }])}
          height={200}
        />
      );
      break;
    case 'bootstrap':
      extra = (
        <>
          <EChart option={fanOption(c)} height={200} />
          <div className="stats-row">
            <Stat label="Drawdown p50" value={pct(c.max_dd?.p50, 0)} />
            <Stat label="Drawdown p95" value={pct(c.max_dd?.p95, 0)} tone="down" />
            <Stat label="Size for budget" value={pct(c.size_for_budget, 0)} sub={`keeps p95 within ${pct(c.dd_budget, 0)}`} />
          </div>
        </>
      );
      break;
    case 'significance':
      extra = (
        <>
          <div className="stats-row">
            <Stat label="DSR" term="dsr" value={num(c.dsr, 3)} sub={`N = ${int(c.n_trials)}`} />
            <Stat label="PBO" term="pbo" value={num(c.pbo)} />
            <Stat label="False discovery" term="fdr" value={pct(c.fdr, 0)} sub={`prior ${pct(c.fdr_prior, 0)}`} />
            <Stat label="Min. track record" term="mintrl" value={Number.isFinite(c.min_track_record_years) ? `${num(c.min_track_record_years, 1)} y` : '–'} />
          </div>
          {(c.trial_sharpes ?? []).length > 1 && (
            <EChart
              option={histogramOption(c.trial_sharpes, [
                { name: 'candidate', x: c.sr_annual, color: ACCENT },
                { name: 'luck', x: c.sr0_annual, color: '#fbbf24', label: 'insideStartTop' },
              ])}
              height={170}
            />
          )}
        </>
      );
      break;
    case 'regimes':
      extra = (
        <>
          <EChart option={regimesOption(c)} height={190} />
          <ul className="plain-list small">
            {(c.crises ?? []).map((x: { regime: string; strategy: number; benchmark: number }) => (
              <li key={x.regime}>
                {x.regime}: strategy {pct(x.strategy, 0, true)} vs buy-and-hold {pct(x.benchmark, 0, true)}
              </li>
            ))}
          </ul>
        </>
      );
      break;
    case 'lockbox':
      extra =
        card.verdict === 'candidate' && !card.meta.lockbox_spent ? (
          <LockboxForm card={card} onOpened={onChange} />
        ) : null;
      break;
    default:
      extra = null;
  }
  return (
    <>
      <p className="small" style={{ marginBottom: 8 }}>{stage.summary}</p>
      <p className="faint small" style={{ marginBottom: 10 }}>Rule: {stage.threshold}</p>
      {extra}
    </>
  );
}

/**
 * A full report card: verdict, the ten stage badges, and one panel per stage with its evidence.
 *
 * @param props.card - Report card from the API.
 * @param props.onChange - Receives the card after the lockbox is opened.
 */
export function ReportCard({ card, onChange }: { card: GauntletCard; onChange: (c: GauntletCard) => void }) {
  const m = card.meta;
  return (
    <div className="stack">
      <div className="note">
        <VerdictBadge verdict={card.verdict} /> <b>{m.question}</b>{' '}
        <span className="num faint">{Object.entries(m.params).map(([k, v]) => `${k}=${v}`).join(' ')}</span> · research{' '}
        {m.research[0]} → {m.research[1]} · <b>N = {int(m.n_trials)}</b> trials on {m.symbol} before this run · grid{' '}
        {int(m.grid_size)} settings · thresholds v{card.thresholds.version} · {num(m.elapsed_s, 1)} s
        {card.failed_stage && <> · stopped at <b>{card.stages.find((s) => s.key === card.failed_stage)?.name}</b></>}
      </div>
      <div className="stage-strip">
        {card.stages.map((s, i) => {
          const t = stageTone(s.passed);
          return (
            <a key={s.key} href={`#stage-${s.key}`} className={`stage-pill ${t.tone}`}>
              <span className="faint">{i + 1}</span> {s.name}
            </a>
          );
        })}
      </div>
      <div className="grid cols-2">
        {card.stages.map((s, i) => {
          const t = stageTone(s.passed);
          return (
            <div id={`stage-${s.key}`} key={s.key}>
              <Card
                title={`${i + 1}. ${s.name}`}
                term={TERMS[s.key]}
                actions={<Badge tone={t.tone}>{t.label}</Badge>}
              >
                <StageBody stage={s} card={card} onChange={onChange} />
              </Card>
            </div>
          );
        })}
      </div>
    </div>
  );
}
