import { useEffect, useState } from 'react';
import type { EChartsOption } from 'echarts';
import { post } from '../api';
import { AXIS, GRID_LINE } from '../charts';
import { Badge, type BadgeTone } from '../components/Badge';
import { Card } from '../components/Card';
import { DataTable } from '../components/DataTable';
import { EChart } from '../components/EChart';
import { Icon } from '../components/Icon';
import { Stat } from '../components/Stat';
import { useToast } from '../components/Toast';
import { int, num, pct, utc } from '../format';
import { useApi } from '../hooks';
import {
  RUBRIC,
  type CompareRow,
  type InboxDigest,
  type InboxJob,
  type InboxPaper,
  type InboxSpec,
  type InboxUsage,
} from '../inbox';

const STATUS_TONE: Record<InboxSpec['status'], BadgeTone> = {
  draft: 'info', needs_code: 'warn', approved: 'info', rejected: 'muted', replicated: 'ok', not_replicated: 'bad',
  judged: 'ok',
};

/**
 * Seven dots for the rubric, filled where the paper earns the point. A paper that isn't a trading strategy scores
 * zero, so its dots stay hollow whatever the model marked.
 *
 * @param props.dots - Rubric points, or null before triage.
 * @param props.strategy - Whether the paper is a trading strategy.
 */
function Dots({ dots, strategy }: { dots: Record<string, number> | null; strategy: boolean | null }) {
  const on = (key: string) => !!strategy && !!dots?.[key];
  return (
    <span className="dots" title={strategy === false ? 'Not a trading strategy' : RUBRIC.map((r) => `${on(r.key) ? '●' : '○'} ${r.label}`).join('\n')}>
      {RUBRIC.map((r) => <span key={r.key} className={`rubric-dot ${on(r.key) ? 'on' : ''}`} />)}
    </span>
  );
}

/**
 * Claimed, replicated and post-publication Sharpe ratios side by side for every spec that has been run.
 *
 * @param rows - Comparison rows.
 * @returns ECharts option.
 */
function compareOption(rows: CompareRow[]): EChartsOption {
  const names = rows.map((r) => `${r.template} · ${r.target}`);
  const bar = (name: string, color: string, pick: (r: CompareRow) => number | null) => ({
    name, type: 'bar' as const, data: rows.map(pick), itemStyle: { color }, barMaxWidth: 18,
  });
  return {
    animation: false,
    tooltip: { trigger: 'axis', valueFormatter: (v) => num(v as number) },
    legend: { textStyle: { color: AXIS }, top: 0 },
    grid: { left: 40, right: 12, top: 30, bottom: 60 },
    xAxis: { type: 'category', data: names, axisLabel: { color: AXIS, rotate: 20 } },
    yAxis: { type: 'value', name: 'Sharpe', axisLabel: { color: AXIS }, splitLine: { lineStyle: { color: GRID_LINE } } },
    series: [
      bar('Claimed', '#a78bfa', (r) => r.claimed),
      bar('Replicated in the lab', '#38bdf8', (r) => r.replicated),
      bar('After publication', '#34d399', (r) => r.post_publication),
    ],
  };
}

/**
 * The spec review pane: the paper on the left, the editable spec in the middle, results and actions on the right.
 *
 * @param props.item - The spec.
 * @param props.onChange - Called after any action, to reload.
 */
function SpecReview({ item, onChange }: { item: InboxSpec; onChange: () => void }) {
  const notify = useToast();
  const [busy, setBusy] = useState('');
  const [reason, setReason] = useState('');
  const [edit, setEdit] = useState(JSON.stringify({ template: item.spec.template, target: item.spec.target, params: item.spec.params }, null, 2));
  useEffect(() => {
    setEdit(JSON.stringify({ template: item.spec.template, target: item.spec.target, params: item.spec.params }, null, 2));
    setReason('');
  }, [item.spec_id, item.spec.template, item.spec.target, item.spec.params]);

  async function act(label: string, path: string, body?: unknown) {
    setBusy(label);
    try {
      const out = await post<InboxSpec>(path, body);
      notify(`${label}: ${out.status}${out.card_id ? ` (card ${out.card_id})` : ''}.`, out.status === 'not_replicated' ? 'warn' : 'ok');
      onChange();
    } catch (err) {
      notify(`${label} failed: ${String(err)}`, 'bad');
    } finally {
      setBusy('');
    }
  }

  function review(decision: 'approve' | 'reject') {
    let edits: Record<string, unknown> = {};
    if (decision === 'approve') {
      try {
        edits = JSON.parse(edit);
      } catch {
        notify('The spec edit isn’t valid JSON.', 'bad');
        return;
      }
    }
    void act(decision === 'approve' ? 'Approved' : 'Rejected', `/inbox/specs/${item.spec_id}/review`, { decision, reason, edits });
  }

  const s = item.spec;
  const r = item.results;
  const canRun = ['approved', 'replicated', 'not_replicated'].includes(item.status);
  return (
    <div className="grid cols-3">
      <Card title="The paper">
        <p className="small"><a href={item.url ?? '#'} target="_blank" rel="noreferrer"><b>{item.title}</b></a></p>
        <p className="small muted">{item.abstract}</p>
        <p className="faint small">Paper text is untrusted: it only ever reaches the model as data, and nothing the model writes is run.</p>
      </Card>
      <Card title={`Spec ${item.spec_id}`}>
        <div className="row"><Badge tone={STATUS_TONE[item.status]}>{item.status}</Badge> <Badge tone="muted">fit: {s.fit}</Badge></div>
        <dl className="kv small">
          <dt>Rule</dt><dd>{s.signal}</dd>
          <dt>Rebalance</dt><dd>{s.rebalance}</dd>
          <dt>Differences</dt><dd>{s.differences}</dd>
          <dt>Claimed</dt><dd>Sharpe {num(s.claimed_sharpe)} · CAGR {pct(s.claimed_cagr, 1)} · {s.sample_start ?? '?'} → {s.sample_end ?? '?'} · costs {s.costs_included === null ? '?' : s.costs_included ? 'yes' : 'no'}</dd>
          <dt>Rationale</dt><dd>{s.rationale}</dd>
          <dt>Read from</dt><dd>{s.read ?? 'abstract'}</dd>
        </dl>
        {!!s.notes?.length && <p className="faint small">{s.notes.join(' · ')}</p>}
        <label className="field small">Template, target and parameters (edit before approving)
          <textarea className="code" rows={7} value={edit} onChange={(e) => setEdit(e.target.value)} disabled={!['draft', 'needs_code'].includes(item.status)} />
        </label>
      </Card>
      <Card title="Review and results">
        {['draft', 'needs_code'].includes(item.status) && (
          <div className="form-grid">
            <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="why (required)" />
            <div className="row">
              <button className="btn primary" disabled={!!busy || !reason.trim()} onClick={() => review('approve')}>Approve</button>
              <button className="btn" disabled={!!busy || !reason.trim()} onClick={() => review('reject')}>Reject</button>
            </div>
            {item.status === 'needs_code' && <p className="small warn">No template fits this paper: edit it onto one, or reject it.</p>}
          </div>
        )}
        {item.review_reason && <p className="small muted">Review: {item.review_reason}</p>}
        {r && (
          <dl className="kv small">
            <dt>Verdict</dt><dd><Badge tone={r.verdict === 'replicated' ? 'ok' : r.verdict === 'no_claim' ? 'muted' : 'bad'}>{r.verdict}</Badge></dd>
            <dt>In the lab</dt><dd>{r.in_sample.error ?? `Sharpe ${num(r.in_sample.sharpe)} · edge ${num(r.in_sample.edge_sharpe)} · ${r.in_sample.start} → ${r.in_sample.end}`}</dd>
            <dt>Tolerance</dt><dd>{r.tolerance === null ? '–' : `±${num(r.tolerance)}`}</dd>
            <dt>After publication</dt><dd>{r.post_publication.reason ?? `Sharpe ${num(r.post_publication.sharpe)} · ${r.post_publication.start} → ${r.post_publication.end}`}</dd>
          </dl>
        )}
        {canRun && (
          <div className="row">
            <button className="btn primary" disabled={!!busy} onClick={() => act('Replicated', `/inbox/specs/${item.spec_id}/replicate`)}>
              {busy === 'Replicated' ? 'Running…' : 'Replicate'}
            </button>
            <button className="btn" disabled={!!busy} onClick={() => act('Judged', `/inbox/specs/${item.spec_id}/judge`)}>
              <Icon name="shield" /> {busy === 'Judged' ? 'Judging…' : 'Run the gauntlet'}
            </button>
          </div>
        )}
        {item.card_id && <p className="small">Gauntlet card <b className="num">{item.card_id}</b> (see the Gauntlet page).</p>}
        <p className="faint small">Every replication and gauntlet run is a ledger trial, so it raises N on that instrument or universe.</p>
      </Card>
    </div>
  );
}

/**
 * Research Inbox: arXiv papers scored on the seven-point rubric by a local model, specs drafted onto the lab's
 * templates, the owner's review, replication against each paper's claim, and the digest.
 */
export function ResearchInbox() {
  const papers = useApi<InboxPaper[]>('/inbox/papers?limit=300');
  const specs = useApi<InboxSpec[]>('/inbox/specs');
  const usage = useApi<InboxUsage>('/inbox/usage');
  const compare = useApi<CompareRow[]>('/inbox/compare');
  const latest = useApi<InboxDigest | null>('/inbox/digest');
  const jobs = useApi<InboxJob[]>('/inbox/jobs', 5000);
  const notify = useToast();
  const [paperId, setPaperId] = useState<string | null>(null);
  const [specId, setSpecId] = useState<string | null>(null);
  const spec = useApi<InboxSpec>(specId ? `/inbox/specs/${specId}` : null);
  const [busy, setBusy] = useState('');
  const running = (jobs.data ?? []).find((j) => j.status === 'running');
  const paper = (papers.data ?? []).find((p) => p.arxiv_id === paperId);

  useEffect(() => {
    if (!running) {
      void papers.reload();
      void specs.reload();
      void usage.reload();
      void latest.reload();
    }
  }, [running?.id]);

  async function reloadAll() {
    await Promise.all([papers.reload(), specs.reload(), usage.reload(), compare.reload(), latest.reload(), spec.reload()]);
  }

  async function act(label: string, fn: () => Promise<string>) {
    setBusy(label);
    try {
      notify(await fn(), 'ok');
      await reloadAll();
    } catch (err) {
      notify(`${label} failed: ${String(err)}`, 'bad');
    } finally {
      setBusy('');
    }
  }

  const all = papers.data ?? [];
  const triaged = all.filter((p) => p.score !== null);
  const waiting = (specs.data ?? []).filter((s) => s.status === 'draft' || s.status === 'needs_code');
  const calls = (usage.data?.by_provider ?? []).reduce((a, r) => a + r.calls, 0);
  const minutes = (usage.data?.by_provider ?? []).reduce((a, r) => a + r.seconds, 0) / 60;
  return (
    <div className="page">
      <div className="page-head">
        <h1>Research Inbox</h1>
        <span className="muted">
          New papers from arXiv, scored on a seven-point rubric by a local model, mapped onto the lab’s strategy templates, and
          replicated only after you approve them.
        </span>
      </div>
      <div className="stats-row">
        <Stat label="Papers" value={int(all.length)} sub={`${int(triaged.length)} scored`} />
        <Stat label="Strategies" value={int(triaged.filter((p) => p.is_strategy).length)} sub={`${int(triaged.filter((p) => (p.score ?? 0) >= 5).length)} scoring 5+`} />
        <Stat label="Specs waiting" value={int(waiting.length)} tone={waiting.length ? 'warn' : ''} sub="your review" />
        <Stat label="Model use (30 days)" value={`${int(calls)} calls`} sub={`${num(minutes, 0)} min · ${num(usage.data?.month.cost_usd ?? 0)} USD this month`} />
      </div>
      {latest.data && (
        <div className="note">
          <b>Digest {utc(latest.data.created_at).slice(5, 16)} UTC.</b> {latest.data.headline.join(' ')}
        </div>
      )}
      <Card
        title="Papers"
        actions={
          <>
            <button className="btn primary" disabled={!!busy || !!running}
              onClick={() => act('Inbox run', async () => { await post('/inbox/run', {}); await jobs.reload(); return 'Inbox run started: fetching, scoring and drafting in the background.'; })}>
              <Icon name="play" /> {running ? 'Running…' : 'Run the inbox'}
            </button>
            <button className="btn" disabled={!!busy} onClick={() => act('Digest', async () => { await post('/inbox/digest'); return 'Digest sent.'; })}>Send digest</button>
          </>
        }
      >
        {running && <p className="small muted">{running.log.slice(-1)[0] ?? 'Starting…'}</p>}
        <DataTable
          rows={all}
          rowKey={(p) => p.arxiv_id}
          maxHeight={420}
          onRowClick={(p) => setPaperId(p.arxiv_id)}
          empty="No papers yet. Run the inbox to fetch the newest arXiv papers (the local model must be running: ollama serve)."
          columns={[
            { key: 's', label: 'Score', sort: (p) => p.score ?? -1, render: (p) => <span className="num">{p.score ?? '–'}/7 <Dots dots={p.dots} strategy={p.is_strategy} /></span> },
            { key: 't', label: 'Paper', sort: (p) => p.title, render: (p) => <span className={p.arxiv_id === paperId ? '' : 'muted'}><b>{p.title}</b><br /><span className="faint small">{p.one_line}</span></span> },
            { key: 'a', label: 'Asset', render: (p) => p.asset_class ?? '–' },
            { key: 'd', label: 'Published', sort: (p) => p.published, render: (p) => <span className="num">{String(p.published).slice(0, 10)}</span> },
            { key: 'n', label: 'Specs', align: 'right', render: (p) => int(p.n_specs) },
          ]}
        />
      </Card>
      {paper && (
        <Card title={paper.title} actions={
          <button className="btn" disabled={!!busy} onClick={() => act('Spec', async () => { const s = await post<InboxSpec>(`/inbox/papers/${paper.arxiv_id}/spec`); setSpecId(s.spec_id); return `Spec ${s.spec_id} drafted (${s.status}).`; })}>
            {busy === 'Spec' ? 'Drafting…' : 'Draft a spec'}
          </button>
        }>
          <p className="small muted">{paper.abstract}</p>
          <ul className="plain-list small">
            {paper.is_strategy === false && <li className="warn">Not a trading strategy: scored zero.</li>}
            {RUBRIC.map((r) => <li key={r.key}>{paper.is_strategy && paper.dots?.[r.key] ? '●' : '○'} {r.label}</li>)}
          </ul>
          <p className="small">{paper.why}</p>
          <p className="faint small"><a href={paper.url} target="_blank" rel="noreferrer">{paper.arxiv_id}</a> · {paper.authors.slice(0, 4).join(', ')} · scored by {paper.model ?? '–'}</p>
        </Card>
      )}
      <Card title="Specs">
        <DataTable
          rows={specs.data ?? []}
          rowKey={(s) => s.spec_id}
          maxHeight={300}
          onRowClick={(s) => setSpecId(s.spec_id)}
          empty="No specs yet. The inbox drafts them for papers scoring 5 or more, or draft one from a paper above."
          columns={[
            { key: 'st', label: 'Status', sort: (s) => s.status, render: (s) => <Badge tone={STATUS_TONE[s.status]}>{s.status}</Badge> },
            { key: 'p', label: 'Paper', render: (s) => <span className={s.spec_id === specId ? '' : 'muted'}>{s.title}</span> },
            { key: 'tp', label: 'Template', render: (s) => `${s.spec.template} · ${s.spec.target}` },
            { key: 'c', label: 'Claimed', align: 'right', render: (s) => num(s.spec.claimed_sharpe) },
            { key: 'r', label: 'Lab', align: 'right', render: (s) => num(s.results?.in_sample.sharpe) },
          ]}
        />
      </Card>
      {spec.data && <SpecReview item={spec.data} onChange={() => void reloadAll()} />}
      {!!compare.data?.length && (
        <Card title="Claimed, replicated and after publication">
          <EChart option={compareOption(compare.data)} height={280} />
          <p className="faint small">McLean & Pontiff (2016): published anomalies lose about half their returns after publication.</p>
        </Card>
      )}
    </div>
  );
}
