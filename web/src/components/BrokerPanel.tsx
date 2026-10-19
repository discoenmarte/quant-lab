import { useEffect, useState } from 'react';
import { post, type PaperEntry } from '../api';
import { BROKER_CHANGED, LIMIT_LABELS, type BrokerStatus, type ExecuteResult } from '../broker';
import { int, num, pct, tone, utc } from '../format';
import { useApi } from '../hooks';
import { Badge } from './Badge';
import { Card } from './Card';
import { DataTable } from './DataTable';
import { Icon } from './Icon';
import { Stat } from './Stat';
import { useToast } from './Toast';

/**
 * Broker paper trading on the Trading Desk: the paper accounts, each entry's sleeve, the orders and fills, slippage
 * against the model, and the controls.
 *
 * Reducing risk (pause, flatten, a tighter limit) is one click. Adding risk (enroll, resume, a looser limit) asks for
 * a typed confirmation and a reason, and resuming or loosening waits 24 hours.
 *
 * @param props.entries - The paper book's entries, to choose one to enroll.
 */
export function BrokerPanel({ entries }: { entries: PaperEntry[] }) {
  const st = useApi<BrokerStatus>('/broker/status', 60000);
  const notify = useToast();
  const [busy, setBusy] = useState('');
  const [enrollId, setEnrollId] = useState('');
  const [enrollReason, setEnrollReason] = useState('');
  const [enrollConfirm, setEnrollConfirm] = useState('');
  const [resumeReason, setResumeReason] = useState('');
  const [resumeWord, setResumeWord] = useState('');
  const [limitName, setLimitName] = useState('max_order_notional');
  const [limitValue, setLimitValue] = useState('');
  const [limitReason, setLimitReason] = useState('');
  const [limitWord, setLimitWord] = useState('');

  const { reload } = st;
  useEffect(() => {
    const onChange = () => void reload();
    window.addEventListener(BROKER_CHANGED, onChange);
    return () => window.removeEventListener(BROKER_CHANGED, onChange);
  }, [reload]);

  const data = st.data;
  const enrolled = new Set((data?.sleeves ?? []).map((s) => s.entry_id));
  const candidates = entries.filter((e) => e.status === 'active' && !enrolled.has(e.entry_id));
  const mode = data?.controls.mode ?? 'active';

  async function run(label: string, fn: () => Promise<string>, kind: 'ok' | 'warn' = 'ok') {
    setBusy(label);
    try {
      notify(await fn(), kind);
      window.dispatchEvent(new Event(BROKER_CHANGED));
    } catch (err) {
      notify(`${label} failed: ${String(err)}`, 'bad');
    } finally {
      setBusy('');
    }
  }

  const execute = () =>
    run('Send', async () => {
      const res = await post<ExecuteResult[]>('/broker/execute');
      const sent = res.reduce((a, r) => a + r.orders.filter((o) => o.status !== 'blocked').length, 0);
      const blocked = res.reduce((a, r) => a + r.orders.filter((o) => o.status === 'blocked').length, 0);
      const why = res.find((r) => r.status !== 'sent' && r.notes.length);
      return `${sent} orders sent, ${blocked} blocked` + (why ? `. ${why.entry_id}: ${why.notes[0]}` : '.');
    });
  const sync = () =>
    run('Sync', async () => {
      const res = await post<{ updated: number; fills: number; errors: string[] }>('/broker/sync');
      return `${res.updated} orders updated, ${res.fills} new fills` + (res.errors.length ? `; ${res.errors[0]}` : '.');
    });
  const pauseAll = () =>
    run('Pause', async () => {
      await post('/broker/pause', { scope: 'all', reason: 'paused from the Trading Desk' });
      return 'New orders paused; positions kept.';
    }, 'warn');
  const flatten = (entryId: string) =>
    run('Flatten', async () => {
      const res = await post<{ orders: unknown[] }>(`/broker/flatten/${entryId}`, { reason: 'flattened from the Trading Desk' });
      return `${entryId} paused; ${res.orders.length} sell orders sent.`;
    }, 'warn');
  const enroll = () =>
    run('Enroll', async () => {
      const sl = await post<{ broker: string; capital: number }>('/broker/enroll', {
        entry_id: enrollId, reason: enrollReason, confirm: enrollConfirm,
      });
      setEnrollId('');
      setEnrollReason('');
      setEnrollConfirm('');
      return `Sleeve of ${int(sl.capital)} on ${sl.broker}. Its next decision becomes paper orders.`;
    });
  const resume = () =>
    run('Resume', async () => {
      const row = await post<{ effective_at: string }>('/broker/resume', { scope: 'all', reason: resumeReason, confirm: resumeWord });
      setResumeReason('');
      setResumeWord('');
      return `Resume recorded; trading starts again at ${utc(row.effective_at)} UTC unless something newer overrides it.`;
    });
  const setLimit = () =>
    run('Limit', async () => {
      const out = await post<{ effective_at: string; loosened: string[] }>('/broker/limits', {
        changes: { [limitName]: Number(limitValue) }, reason: limitReason, confirm: limitWord,
      });
      setLimitValue('');
      setLimitReason('');
      setLimitWord('');
      return out.loosened.length ? `Loosening recorded; it takes effect at ${utc(out.effective_at)} UTC.` : 'Limit tightened, in effect now.';
    });

  return (
    <>
      <div className="page-head">
        <h2>Broker paper trading</h2>
        <span className="muted">
          Enrolled entries trade their decisions on paper accounts (Alpaca paper for ETFs, the Binance testnet for crypto).
          No code path can reach a live endpoint.
        </span>
      </div>
      <div className="stats-row">
        <Stat label="Trading" value={mode.toUpperCase()} tone={mode === 'active' ? '' : 'down'}
          sub={data?.controls.reason ? `${data.controls.reason}` : 'kill switch in the top bar'} />
        {Object.entries(data?.brokers ?? {}).map(([name, b]) => (
          <Stat key={name} label={name === 'alpaca' ? 'Alpaca paper' : 'Binance testnet'}
            value={b.account ? `${int(b.account.equity)} ${b.account.currency}` : b.configured ? 'error' : 'no keys'}
            tone={b.error ? 'down' : ''}
            sub={b.error ? b.error.slice(0, 60) : b.reconciliation ? (b.reconciliation.ok ? 'reconciled' : 'MISMATCH') : b.configured ? 'connected' : 'set its keys in .env'} />
        ))}
        <Stat label="Sleeves" value={int(data?.sleeves.length)} sub={`${int(data?.orders.length)} orders`} />
        <Stat label="Slippage vs model" value={data?.slippage.summary.alpaca ? `${num(data.slippage.summary.alpaca.mean_bps, 1)} bps` : '–'}
          sub={data?.slippage.summary.alpaca ? `${data.slippage.summary.alpaca.n} fills; model charges 2 bps` : 'after the first fills'} />
      </div>
      {!!data?.controls.pending.length && (
        <div className="note">
          {data.controls.pending.map((p) => (
            <div key={p.ts + p.action}>
              Pending: <b>{p.action}</b> {p.scope} takes effect {utc(p.effective_at)} UTC ({p.reason}).
            </div>
          ))}
        </div>
      )}
      <Card
        title="Sleeves"
        term="drifting_positions"
        actions={
          <>
            <button className="btn primary" onClick={execute} disabled={!!busy || mode !== 'active' || !data?.sleeves.length}
              title="Send each sleeve's latest decision as paper orders, within the limits">
              <Icon name="play" /> {busy === 'Send' ? 'Sending…' : 'Send decisions'}
            </button>
            <button className="btn" onClick={sync} disabled={!!busy}>{busy === 'Sync' ? 'Syncing…' : 'Sync fills'}</button>
            <button className="btn" onClick={pauseAll} disabled={!!busy || mode !== 'active'}>Pause all</button>
          </>
        }
      >
        <DataTable
          rows={data?.sleeves ?? []}
          rowKey={(s) => s.entry_id}
          empty="No sleeves yet. Enroll a paper-book entry below; its next decision becomes paper orders."
          columns={[
            { key: 'e', label: 'Entry', render: (s) => <b>{entries.find((e) => e.entry_id === s.entry_id)?.question ?? s.entry_id}</b> },
            { key: 'b', label: 'Broker', render: (s) => s.broker },
            { key: 'st', label: 'State', render: (s) => <Badge tone={s.state === 'active' && mode === 'active' ? 'info' : 'warn'}>{mode === 'active' ? s.state : mode}</Badge> },
            { key: 'eq', label: 'Equity', align: 'right', sort: (s) => s.equity, render: (s) => int(s.equity) },
            { key: 'r', label: 'Return', align: 'right', sort: (s) => s.return, render: (s) => <span className={tone(s.return)}>{pct(s.return, 2, true)}</span> },
            { key: 'h', label: 'Holdings', render: (s) => Object.entries(s.holdings).map(([k, q]) => `${k} ${num(q, q % 1 ? 4 : 0)}`).join(' · ') || 'cash' },
            { key: 'd', label: 'Decision', render: (s) => s.last_decision?.as_of ?? '–' },
            { key: 'a', label: '', render: (s) => <button className="btn ghost" onClick={() => flatten(s.entry_id)} disabled={!!busy || !Object.keys(s.holdings).length}>Flatten</button> },
          ]}
        />
      </Card>
      <Card title="Orders">
        <DataTable
          rows={data?.orders ?? []}
          rowKey={(o) => o.client_id}
          maxHeight={320}
          empty="No orders yet."
          columns={[
            { key: 't', label: 'Planned (UTC)', sort: (o) => o.planned_at, render: (o) => <span className="num">{utc(o.planned_at).slice(5, 16)}</span> },
            { key: 's', label: 'Symbol', render: (o) => o.symbol },
            { key: 'side', label: 'Side', render: (o) => <span className={o.side === 'buy' ? 'up' : 'down'}>{o.side}</span> },
            { key: 'q', label: 'Qty', align: 'right', render: (o) => num(o.qty, o.qty % 1 ? 5 : 0) },
            { key: 'tif', label: 'Fill', render: (o) => (o.tif === 'opg' ? 'opening auction' : o.tif) + (o.late ? ' · late' : '') },
            { key: 'st', label: 'Status', render: (o) => <Badge tone={o.status === 'filled' ? 'ok' : o.status === 'blocked' || o.status === 'rejected' ? 'bad' : 'muted'}>{o.status}</Badge> },
            { key: 'p', label: 'Fill price', align: 'right', render: (o) => num(o.avg_price) },
            { key: 'm', label: 'Note', render: (o) => <span className="faint small">{o.purpose === 'flatten' ? 'flatten · ' : ''}{o.message}</span> },
          ]}
        />
      </Card>
      <div className="grid cols-2">
        <Card title="Enroll an entry">
          <div className="form-grid">
            <label className="field">
              Paper-book entry
              <select value={enrollId} onChange={(e) => setEnrollId(e.target.value)}>
                <option value="">Choose…</option>
                {candidates.map((e) => <option key={e.entry_id} value={e.entry_id}>{e.question} · {e.verdict} · {e.entry_id}</option>)}
              </select>
            </label>
            <label className="field">Why<input value={enrollReason} onChange={(e) => setEnrollReason(e.target.value)} placeholder="e.g. measure real fills for the trend-filter near-miss" /></label>
            <label className="field">Type the entry id to confirm<input value={enrollConfirm} onChange={(e) => setEnrollConfirm(e.target.value)} placeholder={enrollId} /></label>
            <div className="row">
              <button className="btn primary" onClick={enroll} disabled={!!busy || !enrollId || enrollConfirm !== enrollId || !enrollReason.trim()}>Enroll</button>
            </div>
            <p className="faint small">
              The sleeve gets {int(data?.controls.limits.sleeve_capital)} of paper money. Orders are sized from raw closes, long-only and
              without leverage; ETF orders join the opening auction when sent before 09:28 New York time.
            </p>
          </div>
        </Card>
        <Card title="Limits and resume">
          <dl className="kv">
            {Object.entries(data?.controls.limits ?? {}).map(([k, v]) => (
              <div key={k} style={{ display: 'contents' }}>
                <dt>{LIMIT_LABELS[k] ?? k}</dt>
                <dd className="num">{k.includes('share') ? pct(v, 0) : num(v, v % 1 ? 2 : 0)}</dd>
              </div>
            ))}
          </dl>
          <div className="form-grid">
            <div className="row">
              <select value={limitName} onChange={(e) => setLimitName(e.target.value)}>
                {Object.keys(LIMIT_LABELS).map((k) => <option key={k} value={k}>{LIMIT_LABELS[k]}</option>)}
              </select>
              <input value={limitValue} onChange={(e) => setLimitValue(e.target.value)} placeholder="new value" />
            </div>
            <input value={limitReason} onChange={(e) => setLimitReason(e.target.value)} placeholder="why" />
            <input value={limitWord} onChange={(e) => setLimitWord(e.target.value)} placeholder="type LOOSEN if it loosens (waits 24 h)" />
            <div className="row"><button className="btn" onClick={setLimit} disabled={!!busy || !limitValue || !limitReason.trim()}>Change limit</button></div>
          </div>
          {mode !== 'active' && (
            <div className="form-grid">
              <p className="small warn">Trading is {mode}. Resuming adds risk: it takes a reason, the word RESUME, and 24 hours.</p>
              <input value={resumeReason} onChange={(e) => setResumeReason(e.target.value)} placeholder="why it is safe to trade again" />
              <input value={resumeWord} onChange={(e) => setResumeWord(e.target.value)} placeholder="type RESUME" />
              <div className="row"><button className="btn" onClick={resume} disabled={!!busy || resumeWord !== 'RESUME' || !resumeReason.trim()}>Ask to resume</button></div>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
