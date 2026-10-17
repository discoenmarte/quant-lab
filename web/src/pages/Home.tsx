import { useState } from 'react';
import { post, type DemoResult, type Overview } from '../api';
import { Badge } from '../components/Badge';
import { Card } from '../components/Card';
import { DataTable } from '../components/DataTable';
import { Icon } from '../components/Icon';
import { Stat } from '../components/Stat';
import { useToast } from '../components/Toast';
import { int, num, pct, tone, utc } from '../format';
import { useApi } from '../hooks';

/**
 * Lab overview: milestone progress, record counts, the demo pipeline, recent activity and research
 * questions with their trial counts.
 */
export function Home() {
  const { data, reload } = useApi<Overview>('/overview', 30000);
  const [demo, setDemo] = useState<DemoResult | null>(null);
  const [running, setRunning] = useState(false);
  const notify = useToast();
  const counts = data?.counts ?? {};

  async function runDemo() {
    setRunning(true);
    try {
      const result = await post<DemoResult>('/demo');
      setDemo(result);
      notify(`Trial ${result.trial_id} recorded. N = ${result.n_trials}.`, 'ok');
      await reload();
    } catch (err) {
      notify(`Demo failed: ${String(err)}`, 'bad');
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Lab overview</h1>
        <span className="muted">Evidence first. Every trial counted.</span>
      </div>

      <Card title="Milestones">
        <div className="milestones">
          {(data?.milestones ?? []).map((m) => (
            <div key={m.id} className={`milestone ${m.status === 'done' ? 'done' : m.status === 'in progress' ? 'progress' : ''}`}>
              <span className="ms-id">{m.id}</span>
              <span>{m.name}</span>
              <span className="faint">{m.status}</span>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid cols-4">
        <Stat label="Trials in the ledger" term="n_trials" value={int(counts.trials)} sub={`${int(counts.questions)} research questions`} />
        <Stat label="Data series" value={int(counts.series)} sub={`${int(counts.sources)} sources`} />
        <Stat label="Gauntlet report cards" value={int(counts.gauntlet_cards)} sub="ten tests each" />
        <Stat label="Audit events" value={int(counts.audit_events)} sub="every action, logged" />
      </div>

      <div className="grid cols-2">
        <Card
          title="Demo pipeline"
          actions={
            <button className="btn primary" onClick={runDemo} disabled={running}>
              <Icon name="play" /> {running ? 'Running…' : 'Run demo'}
            </button>
          }
        >
          <p className="muted" style={{ marginTop: 0 }}>
            Generates synthetic prices, computes metrics, records a trial in the ledger and writes the audit log. Each
            run is a new trial on the same question, so N goes up. Counting every attempt is the habit the lab is
            built on.
          </p>
          {demo && (
            <>
              <ul className="steps">
                {demo.steps.map((s) => (
                  <li key={s.step}>
                    <span className="check">✓</span>
                    {s.step}
                    <span className="ms num">{s.ms} ms</span>
                  </li>
                ))}
              </ul>
              <div className="stats-row">
                <Stat label="Sharpe" term="sharpe" value={num(demo.metrics.sharpe)} tone={tone(demo.metrics.sharpe)} />
                <Stat label="CAGR" term="cagr" value={pct(demo.metrics.cagr, 1, true)} tone={tone(demo.metrics.cagr)} />
                <Stat label="Max drawdown" term="max_dd" value={pct(-demo.metrics.max_dd)} tone="down" />
                <Stat label="Trials on this question" term="n_trials" value={int(demo.n_trials)} />
              </div>
            </>
          )}
        </Card>

        <Card title="Recent activity">
          {data?.recent_audit.length ? (
            <ul className="feed">
              {data.recent_audit.map((e) => (
                <li key={e.id}>
                  <span className="faint num">{utc(e.ts).slice(5)}</span>
                  <span>
                    <Badge tone="info">{e.action}</Badge> <span className="muted">{e.target}</span>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="empty">No activity yet. Run the demo.</div>
          )}
        </Card>
      </div>

      <Card title="Research questions" term="n_trials">
        <DataTable
          rows={data?.questions ?? []}
          rowKey={(q) => q.question}
          empty="No trials yet."
          columns={[
            { key: 'question', label: 'Question', sort: (q) => q.question },
            { key: 'n', label: 'Trials (N)', align: 'right', sort: (q) => q.n_trials, render: (q) => int(q.n_trials) },
            {
              key: 'best',
              label: 'Best Sharpe',
              align: 'right',
              sort: (q) => q.best_sharpe,
              render: (q) => (
                <span>
                  {num(q.best_sharpe)} <span className="faint">of {int(q.n_trials)}</span>
                </span>
              ),
            },
            { key: 'last', label: 'Last trial', sort: (q) => q.last_trial, render: (q) => utc(q.last_trial) },
          ]}
        />
      </Card>
    </div>
  );
}
