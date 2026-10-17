import { useEffect, useMemo, useState } from 'react';
import type { EChartsOption } from 'echarts';
import { post, type BarsResponse, type CoverageRow, type DataSource, type IngestJob, type QualityIssue, type SeriesRow } from '../api';
import { Badge, type BadgeTone } from '../components/Badge';
import { CandleChart, type ChartMarker } from '../components/CandleChart';
import { Card } from '../components/Card';
import { DataTable } from '../components/DataTable';
import { EChart } from '../components/EChart';
import { useToast } from '../components/Toast';
import { int, utc } from '../format';
import { useApi } from '../hooks';

const STATUS_TONE: Record<string, BadgeTone> = { ok: 'ok', warn: 'warn', error: 'bad', running: 'info', never: 'muted' };

/** Bars per year a complete series should have, by source; used to colour the coverage heatmap. */
const EXPECTED_ROWS: Record<string, number> = { yahoo: 252, binance: 365 };

interface Selection {
  source: string;
  symbol: string;
  interval: string;
}

/**
 * Coverage heatmap option: one row per symbol, one column per year, colour = share of expected bars.
 *
 * @param rows - Coverage records from the API.
 * @returns ECharts option.
 */
function coverageOption(rows: CoverageRow[]): EChartsOption {
  const years = [...new Set(rows.map((r) => r.year))].sort((a, b) => a - b);
  const symbols = [...new Set(rows.map((r) => r.symbol))].sort().reverse();
  const data = rows.map((r) => [
    years.indexOf(r.year),
    symbols.indexOf(r.symbol),
    Math.min(r.n_rows / (EXPECTED_ROWS[r.source] ?? 252), 1),
    r.n_rows,
  ]);
  return {
    tooltip: {
      formatter: (p: unknown) => {
        const v = (p as { value: number[] }).value;
        return `${symbols[v[1]]} · ${years[v[0]]}<br/>${v[3]} bars`;
      },
    },
    grid: { left: 72, right: 12, top: 6, bottom: 26 },
    xAxis: { type: 'category', data: years.map(String), axisLabel: { color: '#8a97a8', fontSize: 10 } },
    yAxis: { type: 'category', data: symbols, axisLabel: { color: '#8a97a8', fontSize: 10 } },
    visualMap: { min: 0, max: 1, show: false, inRange: { color: ['#3b1d1d', '#155e75', '#38bdf8'] } },
    series: [{ type: 'heatmap', data, itemStyle: { borderColor: '#0b0f14', borderWidth: 1 } }],
  };
}

/**
 * Data Health: source status and re-ingest controls, coverage heatmap, series catalog, a candle inspector
 * with dividend and split markers, and the open quality issues.
 */
export function DataHealth() {
  const sources = useApi<DataSource[]>('/data/sources', 10000);
  const series = useApi<SeriesRow[]>('/data/series', 15000);
  const issues = useApi<QualityIssue[]>('/data/issues?limit=300', 15000);
  const coverage = useApi<CoverageRow[]>('/data/coverage?interval=1d', 60000);
  const jobs = useApi<IngestJob[]>('/data/jobs', 3000);
  const notify = useToast();

  const [sel, setSel] = useState<Selection | null>(null);
  const [adjusted, setAdjusted] = useState(false);
  const barsPath = sel
    ? `/data/bars?source=${sel.source}&symbol=${sel.symbol}&interval=${sel.interval}&adjusted=${adjusted}&limit=1500`
    : null;
  const bars = useApi<BarsResponse>(barsPath);

  useEffect(() => {
    if (sel || !series.data?.length) return;
    const spy = series.data.find((s) => s.symbol === 'SPY' && s.interval === '1d');
    const first = spy ?? series.data.find((s) => s.kind === 'bars');
    if (first) setSel({ source: first.source, symbol: first.symbol, interval: first.interval });
  }, [series.data, sel]);

  const running = jobs.data?.find((j) => j.status === 'running');

  async function ingestNow(source?: string) {
    try {
      await post<IngestJob>('/data/ingest', { source: source ?? null });
      notify(`Ingest started${source ? ` for ${source}` : ''}.`, 'info');
      await jobs.reload();
    } catch (err) {
      notify(String(err).includes('409') ? 'An ingest is already running.' : `Ingest failed: ${String(err)}`, 'warn');
    }
  }

  const markers = useMemo<ChartMarker[]>(
    () =>
      (bars.data?.events ?? []).map((e) => ({
        time: e.time,
        position: 'aboveBar',
        color: e.kind === 'dividend' ? '#a78bfa' : '#fbbf24',
        shape: 'circle',
        text: e.kind === 'dividend' ? `D ${e.value.toFixed(2)}` : `S ${e.value}`,
      })),
    [bars.data],
  );

  const coverageRows = coverage.data ?? [];
  const nSymbols = new Set(coverageRows.map((r) => r.symbol)).size;

  return (
    <div className="page">
      <div className="page-head">
        <h1>Data Health</h1>
        <span className="muted">Free sources, quality-checked on every ingest.</span>
        <span style={{ marginLeft: 'auto' }}>
          <button className="btn primary" onClick={() => ingestNow()} disabled={!!running}>
            {running ? 'Ingesting…' : 'Ingest all sources'}
          </button>
        </span>
      </div>

      {running && (
        <div className="note">
          Ingest {running.id} running ({running.sources.join(', ')}). Latest: {running.log.at(-1) ?? 'starting…'}
        </div>
      )}

      <div className="grid cols-3">
        {(sources.data ?? []).map((s) => (
          <Card
            key={s.name}
            title={s.name}
            actions={
              <>
                <Badge tone={STATUS_TONE[s.status] ?? 'muted'}>{s.status}</Badge>
                <button className="btn" onClick={() => ingestNow(s.name)} disabled={!!running}>
                  Re-ingest
                </button>
              </>
            }
          >
            <p className="muted" style={{ marginTop: 0 }}>{s.description}</p>
            <dl className="kv">
              <dt>Last success</dt>
              <dd className="num">{utc(s.last_success)}</dd>
              <dt>Series</dt>
              <dd className="num">{int(s.n_series)}</dd>
              <dt>Universe</dt>
              <dd>{s.universe.length} symbols × {s.intervals.join(', ')}</dd>
              <dt>Message</dt>
              <dd className="muted">{s.message}</dd>
            </dl>
          </Card>
        ))}
      </div>

      <Card title="Coverage: share of expected daily bars per year" term="survivorship">
        {coverageRows.length ? (
          <EChart option={coverageOption(coverageRows)} height={Math.max(160, nSymbols * 17 + 40)} />
        ) : (
          <div className="empty">No data yet. Run an ingest.</div>
        )}
      </Card>

      <div className="grid cols-2">
        <Card title="Series">
          <DataTable
            rows={series.data ?? []}
            rowKey={(s) => `${s.source}/${s.symbol}/${s.interval}`}
            maxHeight={430}
            onRowClick={(s) => s.kind === 'bars' && setSel({ source: s.source, symbol: s.symbol, interval: s.interval })}
            empty="No series yet."
            columns={[
              { key: 'symbol', label: 'Symbol', sort: (s) => s.symbol },
              { key: 'source', label: 'Source', sort: (s) => s.source },
              { key: 'interval', label: 'Int.' },
              { key: 'first', label: 'From', sort: (s) => s.first_ts, render: (s) => <span className="num">{s.first_ts?.slice(0, 10)}</span> },
              { key: 'last', label: 'To', sort: (s) => s.last_ts, render: (s) => <span className="num">{s.last_ts?.slice(0, 10)}</span> },
              { key: 'rows', label: 'Rows', align: 'right', sort: (s) => s.n_rows, render: (s) => int(s.n_rows) },
              {
                key: 'q',
                label: 'Quality',
                sort: (s) => s.n_errors * 1000 + s.n_warnings,
                render: (s) =>
                  s.n_errors ? <Badge tone="bad">{s.n_errors} err</Badge> : s.n_warnings ? <Badge tone="warn">{s.n_warnings} warn</Badge> : <Badge tone="ok">clean</Badge>,
              },
            ]}
          />
        </Card>

        <Card
          title={sel ? `Inspector · ${sel.symbol} ${sel.interval}` : 'Inspector'}
          actions={
            bars.data?.has_adjusted ? (
              <label className="row" style={{ fontSize: 12 }}>
                <input type="checkbox" checked={adjusted} onChange={(e) => setAdjusted(e.target.checked)} />
                dividend-adjusted
              </label>
            ) : undefined
          }
        >
          {bars.data?.bars.length ? (
            <>
              <CandleChart bars={bars.data.bars} markers={markers} intraday={sel?.interval !== '1d'} height={360} />
              <p className="faint" style={{ margin: '8px 0 0', fontSize: 12 }}>
                Last {int(bars.data.bars.length)} of {int(bars.data.n_total)} bars. Purple = dividend, amber = split.
                Backtests use adjusted prices for returns; orders would use raw prices.
              </p>
            </>
          ) : (
            <div className="empty">{bars.error ?? 'Pick a series.'}</div>
          )}
        </Card>
      </div>

      <Card title="Quality issues">
        <DataTable
          rows={issues.data ?? []}
          rowKey={(i, n) => `${i.source}/${i.symbol}/${i.check_name}/${i.ts}/${n}`}
          maxHeight={360}
          empty="No open issues."
          columns={[
            { key: 'sev', label: 'Severity', sort: (i) => i.severity, render: (i) => <Badge tone={i.severity === 'error' ? 'bad' : 'warn'}>{i.severity}</Badge> },
            { key: 'symbol', label: 'Series', sort: (i) => i.symbol, render: (i) => `${i.symbol} ${i.interval}` },
            { key: 'check', label: 'Check', sort: (i) => i.check_name, render: (i) => i.check_name },
            { key: 'ts', label: 'Bar', sort: (i) => i.ts, render: (i) => <span className="num">{i.ts ? i.ts.slice(0, 16).replace('T', ' ') : 'series'}</span> },
            { key: 'detail', label: 'Detail', render: (i) => <span className="muted">{i.detail}</span> },
          ]}
        />
      </Card>
    </div>
  );
}
