import { useEffect, useMemo, useState } from 'react';
import type { EChartsOption } from 'echarts';
import { post, type CalibrationJob, type CalibrationReport, type CalibrationSummary } from '../api';
import { int, num, pct } from '../format';
import { useApi } from '../hooks';
import { Card } from './Card';
import { DataTable } from './DataTable';
import { EChart } from './EChart';
import { Stat } from './Stat';
import { useToast } from './Toast';

const AXIS = '#8a97a8';
const GRID_LINE = '#151c26';
const COLORS = ['#38bdf8', '#34d399', '#a78bfa', '#fbbf24'];

/** Thresholds the what-if panel lets you vary, with labels. */
const WHAT_IF: { key: string; label: string; step: number }[] = [
  { key: 'perm_max_p', label: 'Permutation p ≤', step: 0.005 },
  { key: 'dsr_min', label: 'DSR ≥', step: 0.01 },
  { key: 'pbo_max', label: 'PBO ≤', step: 0.05 },
  { key: 'plateau_min', label: 'Plateau ≥', step: 0.05 },
  { key: 'wf_min_ratio', label: 'Walk-forward kept ≥', step: 0.05 },
  { key: 'regime_min_share', label: 'Regimes positive ≥', step: 0.05 },
];

/**
 * Power curves: detection rate against history length, one line per family and planted edge.
 *
 * @param s - Calibration summary.
 * @returns ECharts option.
 */
function powerOption(s: CalibrationSummary): EChartsOption {
  const keys = [...new Set(s.power.map((p) => `${p.family} · edge ${num(p.true_edge)}`))];
  return {
    animation: false,
    legend: { data: keys, textStyle: { color: AXIS }, top: 0 },
    tooltip: { trigger: 'axis', valueFormatter: (v) => pct(v as number, 0) },
    grid: { left: 44, right: 16, top: 30, bottom: 30 },
    xAxis: { type: 'value', name: 'years', nameLocation: 'middle', nameGap: 20, axisLabel: { color: AXIS }, splitLine: { show: false } },
    yAxis: { type: 'value', min: 0, max: 1, axisLabel: { color: AXIS, formatter: (v: number) => `${Math.round(v * 100)}%` }, splitLine: { lineStyle: { color: GRID_LINE } } },
    series: [
      ...keys.map((k, i) => ({
        name: k,
        type: 'line' as const,
        data: s.power.filter((p) => `${p.family} · edge ${num(p.true_edge)}` === k).sort((a, b) => a.years - b.years).map((p) => [p.years, p.rate]),
        lineStyle: { color: COLORS[i % COLORS.length] },
        itemStyle: { color: COLORS[i % COLORS.length] },
      })),
      {
        name: 'false positives',
        type: 'line',
        data: [[Math.min(...s.power.map((p) => p.years)), s.fpr_overall], [Math.max(...s.power.map((p) => p.years)), s.fpr_overall]],
        lineStyle: { color: '#f87171', type: 'dashed' },
        showSymbol: false,
      },
    ],
  };
}

/**
 * Calibration: the gauntlet's false-positive rate on noise and its power on planted edges, with a what-if
 * panel that re-judges the saved runs under other thresholds.
 */
export function CalibrationPanel() {
  const reports = useApi<CalibrationReport[]>('/gauntlet/calibrations', 0);
  const jobs = useApi<CalibrationJob[]>('/gauntlet/calibrations/jobs', 3000);
  const notify = useToast();
  const latest = reports.data?.[0] ?? null;
  const running = jobs.data?.find((j) => j.status === 'running');
  const [reps, setReps] = useState({ noise_reps: 100, power_reps: 40 });
  const [changes, setChanges] = useState<Record<string, number>>({});
  const [whatIf, setWhatIf] = useState<CalibrationSummary | null>(null);

  const finished = jobs.data?.filter((j) => j.status === 'done').length ?? 0;
  const reloadReports = reports.reload;
  useEffect(() => {
    if (finished) void reloadReports();
  }, [finished, reloadReports]);

  async function start() {
    try {
      await post<CalibrationJob>('/gauntlet/calibrations', reps);
      notify('Calibration started. It uses every core but one for several minutes.', 'info');
      await jobs.reload();
    } catch (err) {
      notify(`Could not start: ${String(err)}`, 'bad');
    }
  }

  async function rejudge() {
    if (!latest) return;
    try {
      setWhatIf(await post<CalibrationSummary>(`/gauntlet/calibrations/${latest.id}/rejudge`, { changes }));
    } catch (err) {
      notify(`What-if failed: ${String(err)}`, 'bad');
    }
  }

  const shown: CalibrationSummary | null = whatIf ?? latest;
  const noiseRows = useMemo(() => shown?.noise ?? [], [shown]);

  return (
    <div className="stack">
      <Card
        title="Calibration"
        term="calibration"
        actions={
          <>
            <label className="faint small">
              noise runs <input type="number" style={{ width: 70 }} value={reps.noise_reps} onChange={(e) => setReps({ ...reps, noise_reps: Number(e.target.value) })} />
            </label>
            <label className="faint small">
              edge runs <input type="number" style={{ width: 70 }} value={reps.power_reps} onChange={(e) => setReps({ ...reps, power_reps: Number(e.target.value) })} />
            </label>
            <button className="btn primary" disabled={!!running} onClick={start}>
              {running ? 'Running…' : 'Run calibration'}
            </button>
          </>
        }
      >
        {running && <pre className="faint small">{running.log.slice(-6).join('\n')}</pre>}
        {!latest && !running && <div className="empty">No calibration yet. Run one to measure the gauntlet's error rates.</div>}
        {latest && (
          <>
            <p className="lead" style={{ marginBottom: 12 }}>{latest.headline}</p>
            <div className="stats-row">
              <Stat label="False positives" value={pct(shown?.fpr_overall, 1)} tone={shown && shown.fpr_overall > 0.05 ? 'down' : ''} sub="target ≤ 5%" />
              <Stat label="Runs" value={int(latest.settings.n_runs)} sub={`${int(latest.settings.n_trials)} trials recorded`} />
              <Stat label="Thresholds" value={`v${latest.thresholds_version}`} sub={latest.created_at.replace('T', ' ')} />
              <Stat label="Took" value={`${num(latest.elapsed_s / 60, 1)} min`} />
            </div>
          </>
        )}
      </Card>

      {latest && shown && (
        <>
          <div className="grid cols-2">
            <Card title={whatIf ? 'Power vs history length (what-if)' : 'Power vs history length'}>
              <EChart option={powerOption(shown)} height={260} />
              <p className="faint small">
                Share of planted-edge markets admitted, by years of data. The dashed line is the rate at which noise is
                admitted.
              </p>
            </Card>
            <Card title="What if the thresholds were different?">
              <div className="form-grid">
                {WHAT_IF.map((w) => (
                  <label key={w.key} className="row" style={{ justifyContent: 'space-between' }}>
                    <span className="muted small">{w.label}</span>
                    <input
                      type="number"
                      step={w.step}
                      style={{ width: 110 }}
                      value={changes[w.key] ?? Number(latest.thresholds[w.key])}
                      onChange={(e) => setChanges({ ...changes, [w.key]: Number(e.target.value) })}
                    />
                  </label>
                ))}
                <div className="row">
                  <button className="btn" onClick={rejudge}>Re-judge saved runs</button>
                  {whatIf && (
                    <button className="btn ghost" onClick={() => { setWhatIf(null); setChanges({}); }}>
                      Back to actual
                    </button>
                  )}
                </div>
                <p className="faint small">
                  Nothing is re-run or saved. To adopt new thresholds, change them on the Thresholds tab with a reason.
                </p>
              </div>
            </Card>
          </div>

          <Card title="Noise: how often luck passes">
            <DataTable
              rows={noiseRows}
              rowKey={(r) => `${r.family}-${r.scenario}`}
              columns={[
                { key: 'family', label: 'Family', sort: (r) => r.family },
                { key: 'scenario', label: 'Researcher', render: (r) => (r.scenario === 'noise_fixed' ? 'fixed in advance (N = 1)' : 'kept the best of the grid') },
                { key: 'years', label: 'Years', align: 'right', render: (r) => int(r.years) },
                { key: 'adm', label: 'Admitted', align: 'right', render: (r) => `${r.admitted} / ${r.n}` },
                { key: 'rate', label: 'Rate', align: 'right', sort: (r) => r.rate, render: (r) => <span className={r.rate > 0.05 ? 'down' : ''}>{pct(r.rate, 1)}</span> },
                { key: 'ci', label: '95% interval', align: 'right', render: (r) => `${pct(r.low, 1)}–${pct(r.high, 1)}` },
                { key: 'stopper', label: 'Most rejections at', render: (r) => Object.entries(r.stages).sort((a, b) => a[1] - b[1])[0]?.[0] ?? '' },
              ]}
            />
          </Card>

          <Card title="Planted edges: how often a real edge is caught">
            <DataTable
              rows={shown.power}
              rowKey={(r) => `${r.family}-${r.trend}-${r.years}`}
              columns={[
                { key: 'family', label: 'Family', sort: (r) => r.family },
                { key: 'edge', label: 'True edge Sharpe', align: 'right', sort: (r) => r.true_edge, render: (r) => num(r.true_edge) },
                { key: 'years', label: 'Years', align: 'right', sort: (r) => r.years, render: (r) => int(r.years) },
                { key: 'adm', label: 'Admitted', align: 'right', render: (r) => `${r.admitted} / ${r.n}` },
                { key: 'rate', label: 'Power', align: 'right', sort: (r) => r.rate, render: (r) => pct(r.rate, 0) },
                { key: 'stopper', label: 'Weakest stage', render: (r) => Object.entries(r.stages).sort((a, b) => a[1] - b[1])[0]?.[0] ?? '' },
              ]}
            />
          </Card>

          <Card title="Against random admission (Zheng, 2026)">
            <DataTable
              rows={shown.control.filter((c) => c.years >= 10)}
              rowKey={(r) => `${r.family}-${r.years}-${r.true_edge}-${r.prior}`}
              columns={[
                { key: 'family', label: 'Family' },
                { key: 'edge', label: 'Edge', align: 'right', render: (r) => num(r.true_edge) },
                { key: 'years', label: 'Years', align: 'right', render: (r) => int(r.years) },
                { key: 'prior', label: 'Real ideas', align: 'right', render: (r) => pct(r.prior, 0) },
                { key: 'prec', label: 'Admitted that are real', align: 'right', render: (r) => pct(r.precision, 0) },
                { key: 'rand', label: 'Random admission', align: 'right', render: (r) => pct(r.random_precision, 0) },
                { key: 'lift', label: 'Lift', align: 'right', sort: (r) => r.lift, render: (r) => (r.lift === null ? '–' : `${num(r.lift, 1)}×`) },
              ]}
            />
            <p className="faint small">
              If 5% (or 20%) of tested ideas were real, this is the share of admitted strategies that would be real,
              compared with admitting at random.
            </p>
          </Card>
        </>
      )}
    </div>
  );
}
