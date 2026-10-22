import { useState } from 'react';
import { post } from '../api';
import { Badge } from '../components/Badge';
import { Card } from '../components/Card';
import { useToast } from '../components/Toast';
import { useApi } from '../hooks';

/** One requirement of the go-live checklist, as `GET /api/golive` returns it. */
export interface GateItem {
  key: string;
  group: 'research' | 'paper' | 'broker' | 'plan';
  label: string;
  /** True met, false not met, null not measurable yet. */
  ok: boolean | null;
  detail: string;
}

/** One paper-book entry's checklist. */
export interface GateEntry {
  entry_id: string;
  question: string;
  card_id: string;
  verdict: string;
  items: GateItem[];
  met: number;
  total: number;
  ready: boolean;
  blocking: string[];
  earliest_paper_date: string;
  next_step: string;
}

/** Body of `GET /api/golive`. */
export interface GateOverview {
  entries: GateEntry[];
  ready: string[];
  /** Whether the lab has a live order path at all (it doesn't: decision 0013). */
  live_path: boolean;
  owner: string;
}

const GROUPS: { key: GateItem['group']; label: string }[] = [
  { key: 'research', label: 'Research evidence' },
  { key: 'paper', label: 'Forward paper book' },
  { key: 'broker', label: 'Broker paper trading' },
  { key: 'plan', label: 'Your written plan' },
];

/**
 * The owner's plan for an entry: capital, loss budget, stop rule, affordability, signed with their typed name.
 * Saving it ticks only the plan's own items and changes nothing else.
 *
 * @param props.entry - The entry the plan is for.
 * @param props.owner - The name that signs it.
 * @param props.onSaved - Called after a successful save.
 */
function PlanForm({ entry, owner, onSaved }: { entry: GateEntry; owner: string; onSaved: () => void }) {
  const toast = useToast();
  const [capital, setCapital] = useState('1000');
  const [loss, setLoss] = useState('200');
  const [rule, setRule] = useState('');
  const [affirmed, setAffirmed] = useState(false);
  const [reason, setReason] = useState('');
  const [confirm, setConfirm] = useState('');
  const save = async () => {
    try {
      await post(`/golive/${entry.entry_id}/plan`, {
        capital: Number(capital), loss_budget: Number(loss), stop_rule: rule, affirmed, reason, confirm,
      });
      toast('Plan saved. It changes nothing by itself.', 'ok');
      onSaved();
    } catch (err) {
      toast(String(err), 'bad');
    }
  };
  return (
    <div className="form-grid gate-plan">
      <label>Capital you would put in (USD)<input value={capital} onChange={(e) => setCapital(e.target.value)} /></label>
      <label>Most you accept to lose (USD)<input value={loss} onChange={(e) => setLoss(e.target.value)} /></label>
      <label>Stop rule (empty uses the suggested one)
        <input value={rule} onChange={(e) => setRule(e.target.value)} placeholder="stop if the edge leaves its band…" />
      </label>
      <label className="row">
        <input type="checkbox" checked={affirmed} onChange={(e) => setAffirmed(e.target.checked)} />
        This is money I can afford to lose entirely
      </label>
      <label>Reason<input value={reason} onChange={(e) => setReason(e.target.value)} /></label>
      <label>Type your name ({owner}) to sign<input value={confirm} onChange={(e) => setConfirm(e.target.value)} /></label>
      <div className="row">
        <button className="btn primary" onClick={() => void save()} disabled={!reason || confirm !== owner}>
          Save plan
        </button>
      </div>
    </div>
  );
}

/**
 * One entry's checklist, grouped, with a mark per item and the next step in plain words.
 *
 * @param props.entry - Checklist.
 * @param props.owner - Owner's name for the plan form.
 * @param props.onSaved - Reload after a plan is saved.
 */
function EntryGate({ entry, owner, onSaved }: { entry: GateEntry; owner: string; onSaved: () => void }) {
  const [planning, setPlanning] = useState(false);
  return (
    <Card
      className="gate-card"
      title={<>{entry.question} <span className="muted">{entry.entry_id}</span></>}
      actions={
        <>
          <Badge tone={entry.verdict === 'validated' ? 'ok' : 'bad'}>{entry.verdict}</Badge>
          <Badge tone={entry.ready ? 'ok' : 'warn'}>{entry.met} of {entry.total} met</Badge>
        </>
      }
    >
      <div className="gate-progress"><div style={{ width: `${(100 * entry.met) / entry.total}%` }} /></div>
      <p className="gate-next">{entry.next_step}</p>
      {GROUPS.map((g) => (
        <div key={g.key} className="gate-group">
          <h4>{g.label}</h4>
          <ul className="gate-items">
            {entry.items.filter((i) => i.group === g.key).map((i) => (
              <li key={i.key}>
                <span className={`gate-mark ${i.ok === true ? 'ok' : i.ok === false ? 'bad' : 'wait'}`}>
                  {i.ok === true ? '✓' : i.ok === false ? '✗' : '…'}
                </span>
                <span>{i.label}<span className="muted gate-detail">{i.detail}</span></span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      <div className="row">
        <button className="btn small" onClick={() => setPlanning(!planning)}>
          {planning ? 'Close the plan' : 'Write your plan'}
        </button>
        <span className="muted">Paper time no earlier than {entry.earliest_paper_date}.</span>
      </div>
      {planning && <PlanForm entry={entry} owner={owner} onSaved={() => { setPlanning(false); onSaved(); }} />}
    </Card>
  );
}

/**
 * Go-Live Gate (M8): the roadmap's checklist for every active paper-book entry, computed from the evidence the lab
 * records. Real money needs every item green; until then this page says what is missing and when it could pass.
 * The lab has no live order path (decision 0013), so nothing here can trade.
 */
export function GoLiveGate() {
  const { data, error, reload } = useApi<GateOverview>('/golive', 60000);
  if (error) return <div className="page"><div className="empty">API unreachable: {error}</div></div>;
  if (!data) return <div className="page"><div className="empty">Loading…</div></div>;
  return (
    <div className="page">
      <div className="page-head">
        <h1>Go-Live Gate</h1>
        <span className="muted">Every item must be green before any real money. Nothing on this page trades.</span>
      </div>
      <div className="gate-banner">
        {data.ready.length
          ? `${data.ready.length} entr${data.ready.length === 1 ? 'y has' : 'ies have'} every item green. The live order path isn't built yet: that is the next step, and it needs your explicit approval.`
          : 'No entry is ready for real money. Strategies earn it with a validated gauntlet card, about three months of forward paper results inside their band, measured broker fills and your written plan.'}
      </div>
      {data.entries.length === 0 && <div className="empty">No active paper-book entry yet: add one from the Strategy Board.</div>}
      <div className="grid cols-2">
        {data.entries.map((e) => (
          <EntryGate key={e.entry_id} entry={e} owner={data.owner} onSaved={() => void reload()} />
        ))}
      </div>
    </div>
  );
}
