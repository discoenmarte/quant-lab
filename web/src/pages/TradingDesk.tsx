import { useEffect, useState } from 'react';
import type { EChartsOption } from 'echarts';
import {
  post,
  type PaperBand,
  type PaperBook,
  type PaperEntry,
  type PaperEntryDetail,
  type PaperReport,
  type PaperRunResult,
  type PaperScoreResult,
} from '../api';
import { AXIS, GRID_LINE } from '../charts';
import { Badge, type BadgeTone } from '../components/Badge';
import { BrokerPanel } from '../components/BrokerPanel';
import { Card } from '../components/Card';
import { DataTable } from '../components/DataTable';
import { EChart } from '../components/EChart';
import { Icon } from '../components/Icon';
import { VerdictBadge } from '../components/ReportCard';
import { Stat } from '../components/Stat';
import { useToast } from '../components/Toast';
import { int, num, pct, tone, utc } from '../format';
import { useApi } from '../hooks';

const BAND_FILL = 'rgba(138,151,168,0.16)';

/**
 * Badge for the forward edge Sharpe against the card's research band.
 *
 * @param props.status - Band status, or null before two forward days.
 */
function BandBadge({ status }: { status: PaperBand['status'] }) {
  if (!status) return <Badge tone="muted">no band yet</Badge>;
  const t: BadgeTone = status === 'below' ? 'bad' : status === 'above' ? 'info' : 'ok';
  return <Badge tone={t}>{status === 'inside' ? 'inside band' : `${status} band`}</Badge>;
}

/**
 * Badge for the CUSUM decay alarm.
 *
 * @param props.alarm - Alarm state from the latest scoring, or null.
 */
function AlarmBadge({ alarm }: { alarm: PaperEntry['alarm'] }) {
  if (!alarm) return <Badge tone="muted">not scored</Badge>;
  if (!alarm.applicable) return <Badge tone="muted">n/a</Badge>;
  if (alarm.alarm) return <Badge tone="bad">alarm {alarm.first_alarm}</Badge>;
  return <Badge tone="ok">{`${num(alarm.value, 1)} / ${num(alarm.h, 1)}`}</Badge>;
}

/**
 * Lead time of a decision before the open it fills at.
 *
 * @param hours - Hours from the decision to the open; negative when late.
 * @returns Formatted text.
 */
function lead(hours: number | undefined): string {
  if (hours === undefined || !Number.isFinite(hours)) return '–';
  return hours >= 0 ? `${num(hours, 1)} h before the open` : `${num(-hours, 1)} h late`;
}

/**
 * Band series drawn as a shaded area: an invisible lower line plus the band's width stacked on it.
 *
 * @param name - Legend name.
 * @param t - Times in milliseconds.
 * @param lo - Lower edge.
 * @param hi - Upper edge.
 * @param axis - Grid index.
 * @returns Two line series.
 */
function bandSeries(name: string, t: number[], lo: number[], hi: number[], axis = 0) {
  const stack = `band-${axis}`;
  return [
    { name: `${name} lo`, type: 'line' as const, stack, xAxisIndex: axis, yAxisIndex: axis, showSymbol: false, silent: true,
      lineStyle: { opacity: 0 }, data: t.map((x, i) => [x, lo[i]]), tooltip: { show: false } },
    { name, type: 'line' as const, stack, xAxisIndex: axis, yAxisIndex: axis, showSymbol: false, silent: true,
      lineStyle: { opacity: 0 }, areaStyle: { color: BAND_FILL }, itemStyle: { color: AXIS },
      data: t.map((x, i) => [x, hi[i] - lo[i]]), tooltip: { show: false } },
  ];
}

/**
 * Forward equity of the entry and its benchmark, against the 90% band the research period expects.
 *
 * @param r - Forward report with series.
 * @returns ECharts option.
 */
function equityOption(r: PaperReport): EChartsOption {
  const s = r.series!;
  const t = s.time.map((x) => x * 1000);
  const pair = (ys: number[]) => t.map((x, i) => [x, ys[i]]);
  return {
    animation: false,
    useUTC: true,
    tooltip: { trigger: 'axis', valueFormatter: (v) => pct((v as number) - 1, 2, true) },
    legend: { data: ['Paper book', 'Benchmark', 'Expected', 'Research 90% band'], textStyle: { color: AXIS }, top: 0 },
    grid: { left: 56, right: 16, top: 30, bottom: 28 },
    xAxis: { type: 'time', axisLabel: { color: AXIS }, splitLine: { show: false } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: AXIS, formatter: (v: number) => `${((v - 1) * 100).toFixed(1)}%` }, splitLine: { lineStyle: { color: GRID_LINE } } },
    series: [
      ...bandSeries('Research 90% band', t, s.expected_lo, s.expected_hi),
      { name: 'Expected', type: 'line', showSymbol: false, data: pair(s.expected), lineStyle: { width: 1, type: 'dashed', color: AXIS }, itemStyle: { color: AXIS } },
      { name: 'Benchmark', type: 'line', showSymbol: false, data: pair(s.benchmark), lineStyle: { width: 1.1, color: '#a78bfa' }, itemStyle: { color: '#a78bfa' } },
      { name: 'Paper book', type: 'line', showSymbol: t.length < 40, data: pair(s.equity), lineStyle: { width: 1.8, color: '#38bdf8' }, itemStyle: { color: '#38bdf8' } },
    ],
  };
}

/**
 * Cumulative forward edge against its research band, and the CUSUM statistic against its alarm threshold.
 *
 * @param r - Forward report with series.
 * @returns ECharts option.
 */
function edgeOption(r: PaperReport): EChartsOption {
  const s = r.series!;
  const t = s.time.map((x) => x * 1000);
  const pair = (ys: number[]) => t.map((x, i) => [x, ys[i]]);
  const h = r.alarm?.applicable ? r.alarm.h ?? null : null;
  const withAlarm = h !== null;
  const axisX = (i: number) => ({ type: 'time' as const, gridIndex: i, axisLabel: { show: i === (withAlarm ? 1 : 0), color: AXIS }, splitLine: { show: false } });
  const asPct = { valueFormatter: (v: unknown) => pct(v as number, 2, true) };
  return {
    animation: false,
    useUTC: true,
    tooltip: { trigger: 'axis' },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    legend: { data: ['Forward edge', 'Expected edge', 'Edge 90% band', ...(withAlarm ? ['CUSUM'] : [])], textStyle: { color: AXIS }, top: 0 },
    grid: withAlarm
      ? [{ left: 56, right: 16, top: 30, height: '48%' }, { left: 56, right: 16, top: '70%', height: '20%' }]
      : [{ left: 56, right: 16, top: 30, bottom: 28 }],
    xAxis: withAlarm ? [axisX(0), axisX(1)] : [axisX(0)],
    yAxis: [
      { type: 'value', gridIndex: 0, scale: true, axisLabel: { color: AXIS, formatter: (v: number) => `${(v * 100).toFixed(1)}%` }, splitLine: { lineStyle: { color: GRID_LINE } } },
      ...(withAlarm
        ? [{ type: 'value' as const, gridIndex: 1, min: 0, max: Math.max(h * 1.1, ...s.cusum), axisLabel: { color: AXIS }, splitLine: { lineStyle: { color: GRID_LINE } } }]
        : []),
    ],
    series: [
      ...bandSeries('Edge 90% band', t, s.edge_lo, s.edge_hi),
      { name: 'Expected edge', type: 'line', showSymbol: false, data: pair(s.edge_expected), tooltip: asPct, lineStyle: { width: 1, type: 'dashed', color: AXIS }, itemStyle: { color: AXIS } },
      { name: 'Forward edge', type: 'line', showSymbol: t.length < 40, data: pair(s.edge), tooltip: asPct, lineStyle: { width: 1.6, color: '#34d399' }, itemStyle: { color: '#34d399' } },
      ...(withAlarm
        ? [{
            name: 'CUSUM', type: 'line' as const, xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, data: pair(s.cusum),
            tooltip: { valueFormatter: (v: unknown) => num(v as number, 1) },
            lineStyle: { width: 1.2, color: '#fbbf24' }, itemStyle: { color: '#fbbf24' },
            markLine: { silent: true, symbol: 'none', label: { color: '#f87171', position: 'insideEndTop' as const, formatter: `alarm at ${h.toFixed(1)}` }, lineStyle: { color: '#f87171', type: 'dashed' as const }, data: [{ yAxis: h }] },
          }]
        : []),
    ],
  };
}

/**
 * Horizontal bars of the latest targets, largest first.
 *
 * @param targets - Weight per instrument.
 * @returns ECharts option.
 */
function targetsOption(targets: Record<string, number>): EChartsOption {
  const rows = Object.entries(targets).sort((a, b) => b[1] - a[1]);
  return {
    animation: false,
    tooltip: { trigger: 'axis', valueFormatter: (v) => pct(v as number, 1) },
    grid: { left: 64, right: 16, top: 6, bottom: 20 },
    xAxis: { type: 'value', axisLabel: { color: AXIS, formatter: (v: number) => `${Math.round(v * 100)}%` }, splitLine: { lineStyle: { color: GRID_LINE } } },
    yAxis: { type: 'category', inverse: true, data: rows.map((r) => r[0]), axisLabel: { color: AXIS } },
    series: [{ type: 'bar', data: rows.map((r) => ({ value: r[1], itemStyle: { color: r[1] > 0 ? '#38bdf8' : r[1] < 0 ? '#f87171' : '#273244' } })) }],
  };
}

/**
 * One entry: what the research period expects, the forward record against it, the latest targets and every
 * decision with its timing.
 *
 * @param props.detail - Entry, decisions and latest report.
 * @param props.onRetire - Retires the entry with a reason.
 */
function EntryDetail({ detail, onRetire }: { detail: PaperEntryDetail; onRetire: (reason: string) => void }) {
  const { entry: e, decisions, report: r } = detail;
  const [reason, setReason] = useState('');
  const s = r?.stats?.strategy ?? {};
  const b = r?.stats?.benchmark ?? {};
  const latest = decisions[0];
  const evidence = r?.band.evidence_years;
  return (
    <>
      <div className="note">
        <b>{e.question}</b> · card <b className="num">{e.card_id}</b> <VerdictBadge verdict={e.verdict} /> · in the book since{' '}
        {utc(e.added_at)} · research {r?.reference.research?.join(' → ') ?? '–'}, edge Sharpe {num(e.research_edge_sharpe)}, beta{' '}
        {num(r?.reference.beta)}.{' '}
        {e.verdict !== 'validated' && <>Not a validated strategy: tracked for forward evidence only. </>}
        {evidence != null && (
          <>If the research edge is real, the forward band clears zero after about <b>{num(evidence, 0)} years</b>; the
            book catches breaks and large decay, not small ones.</>
        )}
      </div>
      <div className="stats-row">
        <Stat label="Forward days" value={int(r?.n_days ?? 0)} sub={`${int(r?.days_in_book ?? 0)} days in the book`} />
        <Stat label="Forward return" value={pct(s.total_return, 2, true)} tone={tone(s.total_return)} sub={`benchmark ${pct(b.total_return, 2, true)}`} />
        <Stat label="Forward edge Sharpe" term="forward_edge" value={num(r?.band.forward)}
          sub={r?.band.floor != null ? `band ${num(r.band.floor, 1)} to ${num(r.band.ceiling, 1)}` : 'needs two forward days'} />
        <Stat label="Decay alarm" term="cusum" value={r?.alarm?.applicable ? `${num(r.alarm.value, 1)} / ${num(r.alarm.h, 1)}` : '–'}
          tone={r?.alarm?.alarm ? 'down' : ''}
          sub={r?.alarm?.applicable ? `fires ~${num((r.alarm.arl1_days ?? 0) / 252, 1)} y after an edge vanishes` : r?.alarm?.reason ?? 'not scored yet'} />
        <Stat label="Decisions" term="lead_time" value={int(decisions.length)} sub={`${int(e.late)} late · last ${latest ? lead(latest.lead_hours) : '–'}`} />
      </div>
      {!r?.series && (
        <div className="empty">
          No forward days yet.{' '}
          {latest ? <>The first decision ({latest.as_of}) fills at the open on {utc(latest.next_open)} and is scored once that bar closes.</> : <>Run today's decisions first.</>}
        </div>
      )}
      {r?.series && (
        <>
          <Card title="Forward equity against the research band" term="paper_book">
            <EChart option={equityOption(r)} height={320} />
          </Card>
          <Card title="Edge beyond the benchmark, and the decay alarm" term="cusum">
            <EChart option={edgeOption(r)} height={r.alarm?.applicable ? 360 : 260} />
            <p className="faint small">
              Edge = net return minus the risk-free rate minus the research beta ({num(r.reference.beta)}) times the
              benchmark's excess return. The alarm accumulates evidence that the edge has fallen to zero; it is tuned
              to one false alarm every {num((r.alarm?.arl0_days ?? 2520) / 252, 0)} years.
            </p>
          </Card>
        </>
      )}
      <div className="grid cols-2">
        <Card title={latest ? `Targets decided on ${latest.as_of}` : 'Targets'}>
          {latest ? (
            <EChart option={targetsOption(latest.targets)} height={Math.max(120, Object.keys(latest.targets).length * 20 + 30)} />
          ) : (
            <div className="empty">No decision yet.</div>
          )}
        </Card>
        <Card title="Decisions" term="lead_time">
          <DataTable
            rows={decisions}
            rowKey={(d) => d.as_of}
            maxHeight={360}
            empty="No decision yet."
            columns={[
              { key: 'as_of', label: 'Close', sort: (d) => d.as_of, render: (d) => <span className="num">{d.as_of}</span> },
              { key: 'decided', label: 'Decided', sort: (d) => d.decided_at, render: (d) => <span className="num">{utc(d.decided_at).slice(5)}</span> },
              { key: 'lead', label: 'Timing', render: (d) => <span className={d.lead_hours < 0 ? 'warn' : 'muted'}>{lead(d.lead_hours)}</span> },
              { key: 'gross', label: 'Gross', align: 'right', render: (d) => pct(Object.values(d.targets).reduce((a, x) => a + Math.abs(x), 0), 0) },
            ]}
          />
        </Card>
      </div>
      {e.status === 'active' && (
        <Card title="Retire this entry">
          <div className="row">
            <input style={{ flex: 1 }} placeholder="Why it stops (required, recorded in the audit log)" value={reason} onChange={(ev) => setReason(ev.target.value)} />
            <button className="btn" disabled={!reason.trim()} onClick={() => onRetire(reason)}>Retire</button>
          </div>
          <p className="faint small">Retiring stops new decisions and ends scoring now. Its decisions stay in the book.</p>
        </Card>
      )}
    </>
  );
}

/**
 * Trading Desk: the forward paper book. Every day after the close each entry's targets are recorded before the next
 * bar exists, then scored on the bars that follow, against what the research period expects. Paper only.
 */
export function TradingDesk() {
  const bookState = useApi<PaperBook>('/paper/book');
  const notify = useToast();
  const [selected, setSelected] = useState<string | null>(null);
  const detail = useApi<PaperEntryDetail>(selected ? `/paper/entries/${selected}` : null);
  const [cardId, setCardId] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<'' | 'run' | 'score' | 'add'>('');

  const entries = bookState.data?.entries ?? [];
  const eligible = bookState.data?.eligible ?? [];
  useEffect(() => {
    if (!selected && entries.length) setSelected(entries[0].entry_id);
  }, [entries, selected]);
  useEffect(() => {
    if (!cardId && eligible.length) setCardId(eligible[0].card_id);
  }, [eligible, cardId]);

  async function refresh() {
    await bookState.reload();
    await detail.reload();
  }

  async function act(kind: 'run' | 'score') {
    setBusy(kind);
    try {
      if (kind === 'run') {
        const res = await post<PaperRunResult[]>('/paper/run');
        const refused = res.filter((r) => r.status === 'refused' || r.status === 'error');
        notify(
          `${res.filter((r) => r.status === 'decided').length} decided, ${res.filter((r) => r.status === 'exists').length} already decided` +
            (refused.length ? `, ${refused.length} refused: ${refused[0].message}` : '.'),
          refused.length ? 'warn' : 'ok',
        );
      } else {
        const res = await post<PaperScoreResult[]>('/paper/score');
        const alarms = res.filter((r) => r.alarm).length;
        notify(`${res.filter((r) => r.status === 'scored').length} scored${alarms ? `, ${alarms} decay alarm(s)` : ''}.`, alarms ? 'warn' : 'ok');
      }
      await refresh();
    } catch (err) {
      notify(`Failed: ${String(err)}`, 'bad');
    } finally {
      setBusy('');
    }
  }

  async function add() {
    setBusy('add');
    try {
      const e = await post<PaperEntry>('/paper/entries', { card_id: cardId, note });
      notify(`${e.question} is in the book. Run today's decisions to start its forward clock.`, 'ok');
      setNote('');
      setCardId('');
      setSelected(e.entry_id);
      await refresh();
    } catch (err) {
      notify(`Could not add: ${String(err)}`, 'bad');
    } finally {
      setBusy('');
    }
  }

  async function retire(reason: string) {
    if (!selected) return;
    try {
      await post(`/paper/entries/${selected}/retire`, { reason });
      notify('Entry retired.', 'ok');
      await refresh();
    } catch (err) {
      notify(`Could not retire: ${String(err)}`, 'bad');
    }
  }

  const active = entries.filter((e) => e.status === 'active');
  const lastDecided = entries.map((e) => e.last_decision?.decided_at ?? '').sort().at(-1) || null;
  const alarms = entries.filter((e) => e.alarm?.alarm).length;
  return (
    <div className="page">
      <div className="page-head">
        <h1>Trading Desk</h1>
        <span className="muted">
          The forward paper book: each day's targets are recorded before the next bar exists, then scored as bars arrive.
          Enrolled entries also trade those targets on paper broker accounts. Paper only: no real money anywhere.
        </span>
      </div>
      <div className="stats-row">
        <Stat label="In the book" term="paper_book" value={int(active.length)} sub={`${int(entries.length - active.length)} retired`} />
        <Stat label="Decisions" value={int(entries.reduce((a, e) => a + e.n_decisions, 0))} sub={`${int(entries.reduce((a, e) => a + e.late, 0))} late`} />
        <Stat label="Last decision" value={lastDecided ? utc(lastDecided).slice(5, 16) : '–'} sub="UTC" />
        <Stat label="Forward days" value={int(Math.max(0, ...entries.map((e) => e.n_days)))} sub="longest record" />
        <Stat label="Decay alarms" term="cusum" value={int(alarms)} tone={alarms ? 'down' : ''} />
      </div>
      <Card
        title="The book"
        actions={
          <>
            <button className="btn primary" onClick={() => act('run')} disabled={!!busy || !active.length} title="Decide every active entry for the latest final close">
              <Icon name="play" /> {busy === 'run' ? 'Deciding…' : "Run today's decisions"}
            </button>
            <button className="btn" onClick={() => act('score')} disabled={!!busy || !entries.length}>
              {busy === 'score' ? 'Scoring…' : 'Score'}
            </button>
          </>
        }
      >
        <DataTable
          rows={entries}
          rowKey={(e) => e.entry_id}
          onRowClick={(e) => setSelected(e.entry_id)}
          empty="The book is empty. Add a report card below to start its forward clock."
          columns={[
            { key: 'q', label: 'Question', sort: (e) => e.question, render: (e) => <span className={e.entry_id === selected ? '' : 'muted'}><b>{e.question}</b></span> },
            { key: 'v', label: 'Card', render: (e) => <VerdictBadge verdict={e.verdict} /> },
            { key: 's', label: 'Status', sort: (e) => e.status, render: (e) => <Badge tone={e.status === 'active' ? 'info' : 'muted'}>{e.status}</Badge> },
            { key: 'days', label: 'Forward days', align: 'right', sort: (e) => e.n_days, render: (e) => int(e.n_days) },
            { key: 'ret', label: 'Return', align: 'right', sort: (e) => e.forward_return, render: (e) => <span className={tone(e.forward_return)}>{pct(e.forward_return, 2, true)}</span> },
            { key: 'bench', label: 'Benchmark', align: 'right', sort: (e) => e.benchmark_return, render: (e) => pct(e.benchmark_return, 2, true) },
            { key: 'edge', label: 'Edge Sharpe', align: 'right', sort: (e) => e.band?.forward ?? null, render: (e) => <>{num(e.band?.forward)} <BandBadge status={e.band?.status ?? null} /></> },
            { key: 'alarm', label: 'Decay alarm', render: (e) => <AlarmBadge alarm={e.alarm} /> },
            { key: 'last', label: 'Last decision', sort: (e) => e.last_decision?.as_of ?? null, render: (e) => e.last_decision ? <span className={e.last_decision.lead_hours < 0 ? 'warn' : ''}>{e.last_decision.as_of}</span> : '–' },
          ]}
        />
      </Card>

      {selected && detail.data && <EntryDetail detail={detail.data} onRetire={retire} />}

      <BrokerPanel entries={entries} />

      <div className="grid cols-2">
        <Card title="Add a report card">
          <div className="form-grid">
            <label className="field">
              Card
              <select value={cardId} onChange={(e) => setCardId(e.target.value)} disabled={!eligible.length}>
                {!eligible.length && <option value="">No daily or portfolio cards left to add</option>}
                {eligible.map((c) => (
                  <option key={c.card_id} value={c.card_id}>
                    {c.question} · {c.verdict} · {c.card_id}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Why track it
              <input value={note} placeholder="e.g. best near-miss of the first portfolio batch" onChange={(e) => setNote(e.target.value)} />
            </label>
            <div className="row">
              <button className="btn primary" onClick={add} disabled={!!busy || !cardId}>
                {busy === 'add' ? 'Adding…' : 'Add to the book'}
              </button>
            </div>
            <p className="faint small">
              Any verdict can join: forward days cost nothing and near-misses show what a candidate's forward test will
              look like. Adding re-runs the card's research period once (a ledger trial) to freeze the bands it is
              judged against.
            </p>
          </div>
        </Card>
        <Card title="Nightly job">
          <p className="small muted">
            After every US close: ingest the free sources, record each entry's targets for that close, then score the
            book. Run <code>make paper-nightly</code> once by hand, or <code>make paper-launchd</code> to print a
            launchd job and its install steps (nothing is installed for you).
          </p>
          <ul className="plain-list small">
            <li>Decisions only use closes that are final (US ETFs 30 minutes after 16:00 New York).</li>
            <li>A missed day stays missed: a later run never decides an older close (no backfills).</li>
            <li>Stale data is refused: if the next session has closed but isn't stored, ingest first.</li>
            <li>A decision recorded after the next open is kept and marked late.</li>
          </ul>
        </Card>
      </div>
    </div>
  );
}
