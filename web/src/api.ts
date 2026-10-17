/**
 * Typed access to the Lab API. Every page talks to the backend through these helpers.
 */

/**
 * GET a JSON resource from the Lab API.
 *
 * @param path - Path below `/api`, e.g. `/overview`.
 * @returns The parsed JSON body.
 * @throws Error with the status code and body text when the response is not OK.
 */
export async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

/**
 * POST JSON to the Lab API.
 *
 * @param path - Path below `/api`.
 * @param body - Request payload; omitted when undefined.
 * @returns The parsed JSON body.
 * @throws Error with the status code and body text when the response is not OK.
 */
export async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export interface Clocks {
  utc: string;
  exchange: string;
  exchange_tz: string;
}

export interface Milestone {
  id: string;
  name: string;
  status: 'done' | 'in progress' | 'planned';
}

export interface AuditEvent {
  id: number;
  ts: string;
  actor: string;
  action: string;
  target: string;
  reason: string;
  details: string;
}

export interface QuestionSummary {
  question: string;
  n_trials: number;
  best_sharpe: number | null;
  last_trial: string;
}

export interface Overview extends Clocks {
  milestones: Milestone[];
  counts: Record<string, number>;
  recent_audit: AuditEvent[];
  questions: QuestionSummary[];
}

export interface GlossaryEntry {
  term: string;
  short: string;
  long: string;
}

export interface DemoResult {
  trial_id: string;
  question: string;
  n_trials: number;
  metrics: Record<string, number>;
  steps: { step: string; ms: number }[];
}

export interface DataSource {
  name: string;
  kind: string;
  description: string;
  last_attempt: string | null;
  last_success: string | null;
  status: 'never' | 'running' | 'ok' | 'warn' | 'error';
  message: string;
  n_series: number;
  universe: string[];
  intervals: string[];
}

export interface SeriesRow {
  source: string;
  symbol: string;
  interval: string;
  kind: string;
  first_ts: string;
  last_ts: string;
  n_rows: number;
  updated_at: string;
  data_hash: string;
  n_errors: number;
  n_warnings: number;
}

export interface CoverageRow {
  source: string;
  symbol: string;
  year: number;
  n_rows: number;
}

export interface QualityIssue {
  source: string;
  symbol: string;
  interval: string;
  ts: string | null;
  check_name: string;
  severity: 'error' | 'warn';
  detail: string;
  found_at: string;
}

export interface BarPoint {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface BarsResponse {
  bars: BarPoint[];
  events: { time: number; kind: 'dividend' | 'split'; value: number }[];
  n_total: number;
  adjusted: boolean;
  has_adjusted: boolean;
}

export interface IngestJob {
  id: string;
  status: 'running' | 'done' | 'failed';
  sources: string[];
  started_at: string;
  finished_at: string | null;
  log: string[];
  results: { source: string; status: string; n_series: number; errors: string[] }[];
}

export interface ParamDef {
  default: number;
  low: number;
  high: number;
  step: number;
  doc: string;
  is_int: boolean;
}

export interface StrategyDef {
  name: string;
  family: string;
  description: string;
  reference: string;
  params: Record<string, ParamDef>;
}

export interface Instrument {
  source: string;
  symbol: string;
  interval: string;
  first_ts: string;
  last_ts: string;
  n_rows: number;
}

export interface CostBps {
  commission_bps: number;
  spread_bps: number;
  slippage_bps: number;
}

export interface BacktestReport {
  meta: {
    strategy: string;
    params: Record<string, number>;
    source: string;
    symbol: string;
    interval: string;
    start: string;
    end: string;
    question: string;
    trial_id: string | null;
    n_trials: number;
    costs: CostBps;
  };
  stats: {
    strategy: Record<string, number>;
    benchmark: Record<string, number>;
    n_trades: number;
    win_rate: number | null;
    avg_win: number | null;
    avg_loss: number | null;
  };
  costs: {
    per_year: { commission: number; spread: number; slippage: number };
    cash_per_year: number;
    gross_cagr: number;
    net_cagr: number;
    turnover_per_year: number;
    cost_per_unit: number;
  };
  series: { time: number[]; equity: number[]; benchmark: number[]; drawdown: number[]; position: number[] };
  monthly: { year: number; month: number; ret: number }[];
  trades: {
    entry: string | null;
    exit: string | null;
    entry_price: number;
    exit_price: number | null;
    side: string;
    bars: number;
    ret: number;
  }[];
}

export interface TrialRow {
  trial_id: string;
  created_at: string;
  question: string;
  family: string;
  strategy: string;
  params: Record<string, number>;
  symbol: string;
  start_date: string | null;
  end_date: string | null;
  source: string;
  sharpe: number | null;
  cagr: number | null;
  max_dd: number | null;
  n_obs: number;
  metrics: Record<string, number>;
}

export interface SystemInfo extends Clocks {
  version: string;
  python: string;
  platform: string;
  libraries: Record<string, string>;
  paths: Record<string, string>;
  database_mb: number;
  counts: Record<string, number>;
  services: { name: string; url: string }[];
}
