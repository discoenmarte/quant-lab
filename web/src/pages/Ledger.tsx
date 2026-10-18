import { useCallback, useEffect, useState } from 'react';
import { get, type LedgerCounts, type LockboxState, type QuestionSummary, type TrialRow } from '../api';
import { Badge } from '../components/Badge';
import { Card } from '../components/Card';
import { DataTable } from '../components/DataTable';
import { Stat } from '../components/Stat';
import { useToast } from '../components/Toast';
import { int, num, pct, tone, utc } from '../format';
import { useApi } from '../hooks';

const PAGE = 500;
const SOURCES = ['', 'studio', 'gauntlet', 'lockbox', 'mining', 'calibration', 'demo'];

/**
 * Ledger: every trial the lab has run, grouped by research question, with the lockboxes already spent.
 * The trial count N next to each best Sharpe is the point: the more tries, the less a good number means.
 */
export function Ledger() {
  const counts = useApi<LedgerCounts>('/ledger/counts', 30000);
  const [withCalib, setWithCalib] = useState(false);
  const questions = useApi<QuestionSummary[]>(`/ledger/questions?include_calibration=${withCalib}`, 30000);
  const lock = useApi<LockboxState>('/gauntlet/lockbox', 30000);
  const notify = useToast();
  const [question, setQuestion] = useState('');
  const [source, setSource] = useState('');
  const [rows, setRows] = useState<TrialRow[]>([]);
  const [more, setMore] = useState(false);

  const load = useCallback(
    async (offset: number) => {
      const q = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
      if (question) q.set('question', question);
      if (source) q.set('source', source);
      try {
        const page = await get<TrialRow[]>(`/ledger/trials?${q}`);
        setRows((prev) => (offset ? [...prev, ...page] : page));
        setMore(page.length === PAGE);
      } catch (err) {
        notify(`Could not load trials: ${String(err)}`, 'bad');
      }
    },
    [question, source, notify],
  );

  useEffect(() => {
    void load(0);
  }, [load]);

  const by = counts.data?.by_source ?? {};
  return (
    <div className="page">
      <div className="page-head">
        <h1>Ledger</h1>
        <span className="muted">Every backtest, from every part of the lab. Nothing is ever deleted.</span>
      </div>

      <div className="stats-row">
        <Stat label="Trials" term="n_trials" value={int(counts.data?.total)} />
        {Object.entries(by)
          .sort((a, b) => b[1] - a[1])
          .map(([k, v]) => (
            <Stat key={k} label={k} value={int(v)} />
          ))}
        <Stat label="Lockbox from" term="lockbox" value={lock.data?.sealed_from ?? '–'} sub={`${int(lock.data?.spent.length)} spent`} />
      </div>

      <div className="grid cols-2">
        <Card
          title="Research questions"
          actions={
            <label className="faint small row">
              <input type="checkbox" checked={withCalib} onChange={(e) => setWithCalib(e.target.checked)} /> include calibration
            </label>
          }
        >
          <DataTable
            rows={questions.data ?? []}
            rowKey={(q) => q.question}
            maxHeight={360}
            onRowClick={(q) => setQuestion(q.question === question ? '' : q.question)}
            columns={[
              { key: 'q', label: 'Question', sort: (q) => q.question, render: (q) => <span className={q.question === question ? 'up' : ''}>{q.question}</span> },
              { key: 'n', label: 'N', align: 'right', sort: (q) => q.n_trials, render: (q) => int(q.n_trials) },
              { key: 's', label: 'Best Sharpe', align: 'right', sort: (q) => q.best_sharpe, render: (q) => num(q.best_sharpe) },
              { key: 'e', label: 'Best edge', align: 'right', sort: (q) => q.best_edge_sharpe ?? null, render: (q) => num(q.best_edge_sharpe) },
              { key: 'l', label: 'Last', sort: (q) => q.last_trial, render: (q) => <span className="num faint">{utc(q.last_trial).slice(0, 10)}</span> },
            ]}
          />
          <p className="faint small">A best Sharpe means less the larger its N. Click a question to filter the trials.</p>
        </Card>
        <Card title="Spent lockboxes" term="lockbox">
          <DataTable
            rows={lock.data?.spent ?? []}
            rowKey={(s) => s.question}
            maxHeight={360}
            empty="Every lockbox is still sealed."
            columns={[
              { key: 'q', label: 'Question', render: (s) => s.question },
              { key: 'how', label: 'How', render: (s) => (s.opened_by_card ? <Badge tone="info">opened by the gauntlet</Badge> : <Badge tone="warn">peeked</Badge>) },
              { key: 'when', label: 'First look', render: (s) => <span className="num faint">{utc(s.first_peek).slice(0, 10)}</span> },
              { key: 'n', label: 'Trials', align: 'right', render: (s) => int(s.n_trials) },
            ]}
          />
        </Card>
      </div>

      <Card
        title={`Trials${question ? ` · ${question}` : ''}`}
        actions={
          <>
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              {SOURCES.map((s) => <option key={s} value={s}>{s || 'all sources'}</option>)}
            </select>
            {question && <button className="btn ghost" onClick={() => setQuestion('')}>Clear question</button>}
          </>
        }
      >
        <DataTable
          rows={rows}
          rowKey={(t) => t.trial_id}
          maxHeight={520}
          columns={[
            { key: 'when', label: 'When', sort: (t) => t.created_at, render: (t) => <span className="num">{utc(t.created_at).slice(0, 16)}</span> },
            { key: 'q', label: 'Question', sort: (t) => t.question },
            { key: 'src', label: 'Source', sort: (t) => t.source, render: (t) => <Badge tone="muted">{t.source}</Badge> },
            { key: 'p', label: 'Params', render: (t) => <span className="faint num">{Object.entries(t.params).map(([k, v]) => `${k}=${v}`).join(' ')}</span> },
            { key: 'per', label: 'Period', render: (t) => <span className="faint num">{t.start_date?.slice(0, 7)} → {t.end_date?.slice(0, 7)}</span> },
            { key: 'sh', label: 'Sharpe', align: 'right', sort: (t) => t.sharpe, render: (t) => num(t.sharpe) },
            { key: 'ed', label: 'Edge', align: 'right', sort: (t) => t.edge_sharpe ?? null, render: (t) => num(t.edge_sharpe) },
            { key: 'cagr', label: 'CAGR', align: 'right', sort: (t) => t.cagr, render: (t) => <span className={tone(t.cagr)}>{pct(t.cagr, 1, true)}</span> },
            { key: 'dd', label: 'Max DD', align: 'right', sort: (t) => t.max_dd, render: (t) => pct(t.max_dd === null ? null : -t.max_dd) },
          ]}
        />
        {more && (
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn" onClick={() => load(rows.length)}>Load {PAGE} more</button>
          </div>
        )}
      </Card>
    </div>
  );
}
