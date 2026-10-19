import { useState } from 'react';
import {
  post,
  type AlertChannel,
  type AlertRow,
  type DailyReport,
  type FreshnessRow,
  type HeadlineItem,
  type LifecycleState,
  type PaperEntry,
  type Registry,
  type RegistryCard,
  type ReportPaperEntry,
  type ReportSummary,
} from '../api';
import { Badge, type BadgeTone } from '../components/Badge';
import { Card } from '../components/Card';
import { DataTable } from '../components/DataTable';
import { Icon } from '../components/Icon';
import { InfoTip } from '../components/InfoTip';
import { VerdictBadge } from '../components/ReportCard';
import { Stat } from '../components/Stat';
import { useToast } from '../components/Toast';
import { int, num, nyTime, pct, tone, utc } from '../format';
import { useApi } from '../hooks';

/** Board columns, in pipeline order. Rejected cards sit in the archive below the board. */
const BOARD: LifecycleState[] = ['candidate', 'validated', 'in_paper_book', 'near_miss', 'retired'];

const STAGE_NAMES: Record<string, string> = {
  leakage: 'look-ahead',
  costs: 'costs',
  walk_forward: 'walk-forward',
  plateau: 'plateau',
  permutation: 'permutation',
  bootstrap: 'bootstrap',
  significance: 'significance',
  regimes: 'regimes',
  diversification: 'diversification',
  lockbox: 'lockbox',
};

const SEVERITY_TONE: Record<AlertRow['severity'], BadgeTone> = { good: 'ok', info: 'muted', warn: 'warn', bad: 'bad' };

/**
 * The report's plain answer: one sentence per line, each with a dot coloured by its tone.
 *
 * @param props.items - Headline items of a report.
 */
function Headline({ items }: { items: HeadlineItem[] }) {
  return (
    <ul className="headline">
      {items.map((h, i) => (
        <li key={`${h.topic}-${i}`}>
          <span className={`dot ${h.tone}`} />
          <span>{h.text}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * One report card on the board: question, verdict, the stages it failed, its headline numbers and the owner's
 * actions (put it in the paper book, retire it), each asking for a reason first.
 *
 * @param props.card - The card with its lifecycle state.
 * @param props.onPaper - Puts the card in the paper book with a note.
 * @param props.onRetire - Retires the card with a reason.
 */
function Tile({
  card,
  onPaper,
  onRetire,
}: {
  card: RegistryCard;
  onPaper: (card: RegistryCard, note: string) => Promise<boolean>;
  onRetire: (card: RegistryCard, reason: string) => Promise<boolean>;
}) {
  const [mode, setMode] = useState<'' | 'paper' | 'retire'>('');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const canPaper = card.state !== 'in_paper_book' && card.interval === '1d';
  const canRetire = card.state !== 'retired';

  async function submit() {
    setBusy(true);
    const done = mode === 'paper' ? await onPaper(card, text) : await onRetire(card, text);
    setBusy(false);
    if (done) {
      setMode('');
      setText('');
    }
  }

  return (
    <div className="tile">
      <div className="tile-q">{card.question}</div>
      <div className="row" style={{ gap: 6 }}>
        <VerdictBadge verdict={card.verdict} />
        {card.near_miss && card.state !== 'near_miss' && <Badge tone="warn">near-miss</Badge>}
        <span className="faint num" style={{ fontSize: 11 }}>{card.card_id}</span>
      </div>
      <div className="tile-meta num">
        <span title="Edge Sharpe after costs">edge {num(card.edge)}</span>
        <span title="Permutation p-value">p {num(card.perm_p, 3)}</span>
        <span title="Deflated Sharpe ratio">DSR {num(card.dsr)}</span>
        {card.psr !== null && card.psr !== undefined && (
          <span title="Undeflated PSR: confidence the edge is above zero before deflating for the search (N = 1)">
            PSR {num(card.psr)}
          </span>
        )}
      </div>
      {card.failed.length > 0 && (
        <div className="chips" title="Stages the card failed">
          {card.failed.map((k) => (
            <span key={k} className="chip">{STAGE_NAMES[k] ?? k}</span>
          ))}
        </div>
      )}
      <div className="faint" style={{ fontSize: 11.5 }}>since {utc(card.since)}</div>
      {mode ? (
        <div className="tile-form">
          <input
            autoFocus
            value={text}
            placeholder={mode === 'paper' ? 'Why track it (recorded with the entry)' : 'Why retire it (required)'}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && (mode === 'paper' || text.trim()) && void submit()}
          />
          <div className="tile-actions">
            <button className="btn small primary" disabled={busy || (mode === 'retire' && !text.trim())} onClick={() => void submit()}>
              {busy ? 'Working…' : mode === 'paper' ? 'Add to the paper book' : 'Retire'}
            </button>
            <button className="btn small ghost" disabled={busy} onClick={() => setMode('')}>Cancel</button>
          </div>
        </div>
      ) : (
        (canPaper || canRetire) && (
          <div className="tile-actions">
            {canPaper && (
              <button className="btn small" onClick={() => setMode('paper')} title="Track it in the forward paper book (any verdict)">
                Paper book
              </button>
            )}
            {canRetire && (
              <button className="btn small ghost" onClick={() => setMode('retire')}>Retire</button>
            )}
          </div>
        )
      )}
    </div>
  );
}

/**
 * Badge for a paper entry's forward edge against its research band.
 *
 * @param props.status - Band status, or null before two forward days.
 */
function BandBadge({ status }: { status: ReportPaperEntry['band']['status'] }) {
  if (!status) return <Badge tone="muted">no band yet</Badge>;
  return <Badge tone={status === 'below' ? 'bad' : status === 'above' ? 'info' : 'ok'}>{status === 'inside' ? 'inside band' : `${status} band`}</Badge>;
}

/**
 * Badge for a paper entry's decay alarm.
 *
 * @param props.alarm - Alarm summary, or null before the first scoring.
 */
function AlarmBadge({ alarm }: { alarm: ReportPaperEntry['alarm'] }) {
  if (!alarm) return <Badge tone="muted">not scored</Badge>;
  if (!alarm.applicable) return <Badge tone="muted">n/a</Badge>;
  if (alarm.alarm) return <Badge tone="bad">alarm {alarm.first_alarm}</Badge>;
  return <Badge tone="ok">{`${num(alarm.value, 1)} / ${num(alarm.h, 1)}`}</Badge>;
}

/**
 * Strategy Board: every report card in its lifecycle state, the daily lab report (what was tested, what passed,
 * what is in the paper book and how it is doing, and whether the data is fresh), and the alert log. Paper only:
 * nothing here can place an order.
 */
export function StrategyBoard() {
  const reg = useApi<Registry>('/registry');
  const reports = useApi<ReportSummary[]>('/reports?limit=30');
  const [reportId, setReportId] = useState('');
  const rep = useApi<DailyReport | null>(reportId ? `/report/${reportId}` : '/report/latest');
  const alertLog = useApi<AlertRow[]>('/alerts?limit=50');
  const channels = useApi<AlertChannel[]>('/alerts/channels');
  const notify = useToast();
  const [busy, setBusy] = useState<'' | 'build' | 'test'>('');
  const [showRejected, setShowRejected] = useState(false);

  const r = rep.data;
  const cards = reg.data?.cards ?? [];
  const counts = reg.data?.counts;
  const states = reg.data?.states ?? [];

  async function reloadAll() {
    await Promise.all([reg.reload(), reports.reload(), rep.reload(), alertLog.reload()]);
  }

  async function build() {
    setBusy('build');
    try {
      const made = await post<DailyReport>('/report', { trigger: 'web' });
      notify(`Report ${made.report_id} built${made.alerts.length ? `, ${made.alerts.length} alert(s) raised` : ''}.`, made.alerts.some((a) => a.severity === 'bad') ? 'warn' : 'ok');
      setReportId('');
      await reloadAll();
    } catch (err) {
      notify(`Could not build the report: ${String(err)}`, 'bad');
    } finally {
      setBusy('');
    }
  }

  async function testAlert() {
    setBusy('test');
    try {
      const a = await post<AlertRow>('/alerts/test');
      const failed = Object.entries(a.delivery).filter(([, v]) => v !== 'ok');
      notify(failed.length ? `Test alert: ${failed.map(([k, v]) => `${k} ${v}`).join('; ')}` : `Test alert sent through ${Object.keys(a.delivery).join(', ')}.`, failed.length ? 'warn' : 'ok');
      await alertLog.reload();
    } catch (err) {
      notify(`Test alert failed: ${String(err)}`, 'bad');
    } finally {
      setBusy('');
    }
  }

  async function toPaper(card: RegistryCard, note: string): Promise<boolean> {
    try {
      const e = await post<PaperEntry>('/paper/entries', { card_id: card.card_id, note: note || 'added from the Strategy Board' });
      notify(`${e.question} is in the paper book. Its forward clock starts with the next nightly run.`, 'ok');
      await reg.reload();
      return true;
    } catch (err) {
      notify(`Could not add it: ${String(err)}`, 'bad');
      return false;
    }
  }

  async function retire(card: RegistryCard, reason: string): Promise<boolean> {
    try {
      await post(`/registry/${card.card_id}/retire`, { reason });
      notify(`${card.question} retired.`, 'ok');
      await reg.reload();
      return true;
    } catch (err) {
      notify(`Could not retire it: ${String(err)}`, 'bad');
      return false;
    }
  }

  const label = (key: LifecycleState | null) => states.find((x) => x.key === key)?.label ?? key ?? 'new';
  const rejected = cards.filter((c) => c.state === 'rejected');
  const stale = r?.freshness.filter((f) => f.stale).length ?? 0;
  return (
    <div className="page">
      <div className="page-head">
        <h1>Strategy Board</h1>
        <span className="muted">
          Every report card in its lifecycle state, and the lab's daily report. Paper only; nothing here can place an order.
        </span>
      </div>

      <Card
        title="Daily report"
        term="daily_report"
        className="report-card"
        actions={
          <>
            <select value={reportId} onChange={(e) => setReportId(e.target.value)} disabled={!reports.data?.length} aria-label="Report">
              <option value="">Latest report</option>
              {(reports.data ?? []).map((x) => (
                <option key={x.report_id} value={x.report_id}>
                  {utc(x.created_at)} · {x.trigger}{x.n_alerts ? ` · ${x.n_alerts} alert${x.n_alerts > 1 ? 's' : ''}` : ''}
                </option>
              ))}
            </select>
            <button className="btn primary" onClick={() => void build()} disabled={!!busy} title="Build a report for the time since the last one">
              <Icon name="play" /> {busy === 'build' ? 'Building…' : 'Build report now'}
            </button>
          </>
        }
      >
        {r ? (
          <>
            <p className="muted small" style={{ marginBottom: 12 }}>
              Built {utc(r.created_at)} ({nyTime(r.created_at)}) by the {r.trigger === 'web' ? 'web app' : r.trigger === 'cli' ? 'command line' : 'nightly job'}; covers{' '}
              {r.since ? `everything since ${utc(r.since)}` : 'everything since the lab began'}. Report <span className="num">{r.report_id}</span>.
            </p>
            <Headline items={r.headline} />
          </>
        ) : (
          <div className="empty">
            {rep.loading ? 'Loading…' : 'No report yet. Build one now, or let the nightly job build one after the close.'}
          </div>
        )}
      </Card>

      <div className="stats-row">
        {states.map((s) => (
          <Stat
            key={s.key}
            label={s.label}
            term={s.key === 'near_miss' ? 'near_miss' : s.key === 'candidate' ? 'lifecycle' : undefined}
            value={int(counts?.[s.key])}
            tone={s.key === 'candidate' || s.key === 'validated' ? (counts?.[s.key] ? 'up' : '') : ''}
          />
        ))}
      </div>

      <div className="board">
        {BOARD.map((key) => {
          const s = states.find((x) => x.key === key);
          const group = cards.filter((c) => c.state === key);
          return (
            <section key={key} className="board-col">
              <header className="board-col-head">
                <h3>
                  {s?.label ?? key}
                  {key === 'near_miss' && <InfoTip term="near_miss" />}
                  <span className="count num">{group.length}</span>
                </h3>
                <p>{s?.description}</p>
              </header>
              <div className="board-col-body">
                {group.length ? (
                  group.map((c) => <Tile key={c.card_id} card={c} onPaper={toPaper} onRetire={retire} />)
                ) : (
                  <div className="empty">{key === 'candidate' ? 'Nothing has passed stages 1-9 yet.' : 'None.'}</div>
                )}
              </div>
            </section>
          );
        })}
      </div>

      <Card
        title={`Rejected (${rejected.length})`}
        actions={
          <button className="btn ghost" onClick={() => setShowRejected(!showRejected)} disabled={!rejected.length}>
            {showRejected ? 'Hide' : 'Show'}
          </button>
        }
      >
        {showRejected ? (
          <div className="archive">
            {rejected.map((c) => (
              <Tile key={c.card_id} card={c} onPaper={toPaper} onRetire={retire} />
            ))}
          </div>
        ) : (
          <p className="small muted">
            Cards that failed at least one stage that tests how the rule behaves (costs, walk-forward, plateau, regimes,
            diversification, lockbox), or missed the luck stages by more than a near-miss may (permutation p above 0.10
            or undeflated PSR below 0.80). Kept for the record: they count in every later deflation.
          </p>
        )}
      </Card>

      {r && (
        <>
          <Card title="Paper book against its bands" term="paper_book" actions={<a className="small" href="/desk">Trading Desk →</a>}>
            <DataTable
              rows={r.paper.entries}
              rowKey={(e) => e.entry_id}
              empty="The paper book is empty. Put a card in it from the board above."
              columns={[
                { key: 'q', label: 'Question', render: (e) => <span className={e.status === 'active' ? '' : 'muted'}>{e.question}</span> },
                { key: 'v', label: 'Card', render: (e) => <VerdictBadge verdict={e.verdict} /> },
                { key: 'd', label: 'Days', align: 'right', render: (e) => int(e.n_days) },
                { key: 'ret', label: 'Return', align: 'right', render: (e) => <span className={tone(e.forward_return)}>{pct(e.forward_return, 2, true)}</span> },
                { key: 'b', label: 'Benchmark', align: 'right', render: (e) => pct(e.benchmark_return, 2, true) },
                { key: 'edge', label: 'Edge Sharpe', render: (e) => <>{num(e.band.forward)} <BandBadge status={e.band.status} /></> },
                { key: 'alarm', label: 'Decay alarm', render: (e) => <AlarmBadge alarm={e.alarm} /> },
                { key: 'last', label: 'Last decision', render: (e) => e.last_decision ? <span className={e.last_decision.lead_hours < 0 ? 'warn' : ''}>{e.last_decision.as_of}</span> : '–' },
              ]}
            />
            <p className="faint small" style={{ marginTop: 8 }}>
              From the latest scoring{r.paper.runs.n ? `; ${int(r.paper.runs.decided)} decisions recorded and ${int(r.paper.runs.refused + r.paper.runs.errors)} refused or failed in this period` : ''}.
              Bands are frozen when a card joins; the alarm fires about once in ten years by chance.
            </p>
          </Card>
          <div className="grid cols-2">
            <Card title={stale ? `Data freshness (${stale} stale)` : 'Data freshness'} term="freshness" actions={<a className="small" href="/data">Data Health →</a>}>
              <DataTable
                rows={r.freshness}
                rowKey={(f) => `${f.source}-${f.interval}`}
                columns={[
                  { key: 's', label: 'Source', render: (f: FreshnessRow) => <b>{f.source} {f.interval}</b> },
                  { key: 'o', label: 'Through', render: (f) => <span className="num" title={f.laggard ? `stalest symbol: ${f.laggard}` : ''}>{f.oldest ?? '–'}</span> },
                  { key: 'e', label: 'Expected', render: (f) => <span className="num">{f.expected ?? 'monthly'}</span> },
                  { key: 'b', label: 'Behind', align: 'right', render: (f) => (f.behind === null ? '–' : `${num(f.behind, 0)} / ${f.tolerance} ${f.unit}`) },
                  {
                    key: 'st',
                    label: 'Status',
                    render: (f) => (
                      <span title={`last ingest: ${f.status ?? 'never'}${f.last_success ? `, last success ${utc(f.last_success)}` : ''}`}>
                        {f.stale ? <Badge tone="warn">stale</Badge> : <Badge tone="ok">fresh</Badge>}{' '}
                        {f.status === 'error' && <Badge tone="bad">ingest failed</Badge>}
                      </span>
                    ),
                  },
                ]}
              />
            </Card>
            <Card title="Trials since the previous report" term="n_trials">
              <div className="stats-row" style={{ marginBottom: 12 }}>
                <Stat label="Research trials" value={int(r.trials.research)} sub={`${int(r.trials.questions)} questions`} />
                <Stat label="Calibration" value={int(r.trials.calibration)} sub={`${int(r.trials.calibration_studies.length)} studies finished`} />
                <Stat label="Paper scorings" value={int(r.trials.paper)} />
              </div>
              <DataTable
                rows={r.trials.by_question}
                rowKey={(q) => `${q.question}-${q.source}`}
                maxHeight={320}
                empty="No research trials in this period."
                columns={[
                  { key: 'q', label: 'Question', sort: (q) => q.question, render: (q) => q.question },
                  { key: 's', label: 'Source', render: (q) => <span className="muted">{q.source}</span> },
                  { key: 'n', label: 'Trials', align: 'right', sort: (q) => q.n, render: (q) => int(q.n) },
                  { key: 'N', label: 'N on question', align: 'right', sort: (q) => q.n_total, render: (q) => int(q.n_total) },
                  { key: 'e', label: 'Best edge', align: 'right', sort: (q) => q.best_edge_sharpe, render: (q) => num(q.best_edge_sharpe) },
                ]}
              />
              <p className="faint small" style={{ marginTop: 8 }}>
                A best edge Sharpe means little without its N: the more trials on a question, the higher luck alone reaches.
              </p>
            </Card>
          </div>
          <Card title="Verdicts since the previous report">
            <DataTable
              rows={r.verdicts}
              rowKey={(c) => c.card_id}
              maxHeight={420}
              empty="No new report cards in this period."
              columns={[
                { key: 'q', label: 'Question', render: (c) => c.question },
                { key: 'v', label: 'Verdict', render: (c) => <VerdictBadge verdict={c.verdict} /> },
                { key: 's', label: 'State', render: (c) => <span className="muted">{c.label}{c.near_miss && c.state !== 'near_miss' ? ' (near-miss)' : ''}</span> },
                { key: 'f', label: 'Failed', render: (c) => <span className="faint">{c.failed.map((k) => STAGE_NAMES[k] ?? k).join(', ') || 'none'}</span> },
              ]}
            />
            {r.transitions.filter((t) => t.from).length > 0 && (
              <ul className="plain-list small" style={{ marginTop: 10 }}>
                {r.transitions.filter((t) => t.from).map((t) => (
                  <li key={t.card_id}>{t.question}: {label(t.from)} → {label(t.to)}</li>
                ))}
              </ul>
            )}
          </Card>
        </>
      )}

      <Card
        title="Alerts"
        term="alert_rules"
        actions={
          <button className="btn" onClick={() => void testAlert()} disabled={!!busy}>
            {busy === 'test' ? 'Sending…' : 'Send a test alert'}
          </button>
        }
      >
        <div className="row" style={{ marginBottom: 12 }}>
          {(channels.data ?? []).map((c) => (
            <span key={c.name} title={c.detail}>
              <Badge tone={c.enabled ? 'ok' : 'muted'}>{c.name} {c.enabled ? 'on' : 'off'}</Badge>
            </span>
          ))}
          <span className="faint small">
            Telegram turns on when QLAB_TELEGRAM_TOKEN and QLAB_TELEGRAM_CHAT are set (environment or the data folder's lab.env, never the repo).
          </span>
        </div>
        <DataTable
          rows={alertLog.data ?? []}
          rowKey={(a) => a.alert_id}
          maxHeight={420}
          empty="No alerts yet. The rules: a new candidate, a paper-book decay alarm, stale data, a failed nightly job."
          columns={[
            { key: 't', label: 'Time', render: (a) => <span className="num">{utc(a.created_at)}</span> },
            { key: 's', label: 'Severity', render: (a) => <Badge tone={SEVERITY_TONE[a.severity]}>{a.rule.replace('_', ' ')}</Badge> },
            {
              key: 'm',
              label: 'Alert',
              render: (a) => (
                <div style={{ whiteSpace: 'normal', minWidth: 320 }}>
                  <b>{a.title}</b>
                  <div className="muted small" style={{ whiteSpace: 'pre-line' }}>{a.message}</div>
                </div>
              ),
            },
            {
              key: 'd',
              label: 'Delivery',
              render: (a) => (
                <span className="small">
                  {Object.entries(a.delivery).map(([k, v]) => (
                    <span key={k} className={v === 'ok' ? 'muted' : 'down'} title={v}>{k} {v === 'ok' ? '✓' : '✗'} </span>
                  ))}
                </span>
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
}
