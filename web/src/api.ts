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
  best_edge_sharpe?: number | null;
  first_trial?: string;
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
  /** Portfolio strategies only: the universes it fits (``static``, ``dynamic`` or ``any``). */
  kind?: 'static' | 'dynamic' | 'any';
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
    lockbox?: { sealed_from: string; included: boolean; spent: boolean };
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
  edge_sharpe?: number | null;
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

export type StageKey =
  | 'leakage'
  | 'costs'
  | 'walk_forward'
  | 'plateau'
  | 'permutation'
  | 'bootstrap'
  | 'significance'
  | 'regimes'
  | 'diversification'
  | 'lockbox';

export interface StageResult {
  key: StageKey;
  name: string;
  /** True pass, false fail, null when the stage can't reject (sizing, sealed, not applicable). */
  passed: boolean | null;
  value: number | null;
  threshold: string;
  summary: string;
  /** Chart payload; its shape differs per stage, and each stage's panel reads only its own fields. */
  chart: Record<string, any> | null;
}

export type Verdict = 'candidate' | 'rejected' | 'validated';

export interface GauntletCard {
  card_id: string;
  created_at: string;
  verdict: Verdict;
  failed_stage: StageKey | null;
  meta: {
    question: string;
    family: string;
    strategy: string;
    params: Record<string, number>;
    source: string;
    symbol: string;
    interval: string;
    costs: CostBps;
    research: [string, string];
    lockbox_from: string;
    lockbox_spent: boolean;
    grid_size: number;
    n_trials: number;
    seed: number;
    elapsed_s: number;
    timings: Record<string, number>;
  };
  thresholds: Record<string, number | string> & { version: number };
  stages: StageResult[];
}

export interface CardRow {
  card_id: string;
  created_at: string;
  question: string;
  family: string;
  strategy: string;
  params: Record<string, number>;
  symbol: string;
  verdict: Verdict;
  stages: { key: StageKey; passed: boolean | null; value: number | null }[];
  thresholds_version: number;
}

export interface FunnelRow {
  key: StageKey;
  name: string;
  reached: number;
  passed: number;
  failed: number;
}

export interface ThresholdState {
  version: number;
  config: Record<string, number | string>;
  history: { version: number; created_at: string; reason: string; config: Record<string, number | string> }[];
}

export interface LockboxState {
  sealed_from: string;
  research_end: string;
  spent: { question: string; first_peek: string; n_trials: number; opened_by_card: string | null }[];
}

export interface RateRow {
  n: number;
  admitted: number;
  rate: number;
  low: number;
  high: number;
  stages: Record<StageKey, number>;
}

export interface CalibrationSummary {
  noise: (RateRow & { family: string; scenario: 'noise_fixed' | 'noise_tuned' | 'noise_batch'; years: number })[];
  power: (RateRow & {
    family: string;
    scenario?: 'power' | 'power_tuned' | 'power_batch';
    trend: number;
    true_edge: number;
    years: number;
  })[];
  control: {
    family: string;
    years: number;
    true_edge: number;
    prior: number;
    power: number;
    fpr: number;
    precision: number | null;
    random_precision: number;
    lift: number | null;
  }[];
  fpr_overall: number;
}

export interface CalibrationReport extends CalibrationSummary {
  id: string;
  created_at: string;
  thresholds_version: number;
  thresholds: Record<string, number | string>;
  settings: {
    noise_reps: number;
    power_reps: number;
    years: number[];
    noise_years: number;
    edges: number[];
    trend_sizes: Record<string, number>;
    n_runs: number;
    n_trials: number;
  };
  headline: string;
  elapsed_s: number;
}

export interface CalibrationJob {
  id: string;
  status: 'running' | 'done' | 'failed';
  started_at: string;
  finished_at: string | null;
  log: string[];
  report_id: string | null;
}

export interface LedgerCounts {
  total: number;
  by_source: Record<string, number>;
}

export interface PortfolioUniverse {
  name: string;
  description: string;
  /** ``static``: fixed members that all trade every day; ``dynamic``: point-in-time top N by dollar volume. */
  kind: 'static' | 'dynamic';
  /** A static universe's instruments; empty for a dynamic one. */
  members: string[];
  /** Members per date of a dynamic universe; null for a static one. */
  top: number | null;
  /** Instruments that are ever members. */
  n_instruments: number | null;
  /** Instruments whose last bar is before the panel's last date (delisted or halted). */
  n_delisted: number | null;
  /** First date of the panel; null when something isn't ingested. */
  first: string | null;
  n_days: number;
}

/** How a portfolio report's universe behaves over time. */
export interface UniverseStats {
  kind: 'static' | 'dynamic';
  top: number | null;
  n_instruments: number;
  n_delisted: number;
  /** Members per point of ``time``. */
  members: number[];
  time: number[];
}

export interface PortfolioReport {
  meta: {
    question: string;
    strategy: string;
    family: string;
    params: Record<string, number>;
    universe: string;
    symbols: string[];
    start: string;
    end: string;
    trial_id: string | null;
    n_trials: number;
    lockbox: { sealed_from: string; included: boolean; spent: boolean };
  };
  stats: { strategy: Record<string, number>; benchmark: Record<string, number> };
  series: { time: number[]; equity: number[]; benchmark: number[]; drawdown: number[]; gross: number[] };
  /** Weight held per instrument at each point of ``series.time``. */
  weights: Record<string, number[]>;
  latest: Record<string, number>;
  /** Average yearly return contributed by each instrument. */
  contribution: Record<string, number>;
  monthly: { year: number; month: number; ret: number }[];
  costs: { per_year: number; cash_per_year: number; turnover_per_year: number };
  /** Membership over time; absent in reports saved before M3c. */
  universe?: UniverseStats;
}

/** One decision of the paper book: the target an entry decided at one close, before the next bar existed. */
export interface PaperDecision {
  entry_id: string;
  /** Session date of the close the decision used. */
  as_of: string;
  /** UTC time the decision was recorded. */
  decided_at: string;
  /** Weight per instrument (a position in [-1, 1] for a single instrument). */
  targets: Record<string, number>;
  /** UTC time of the first open the decision fills at. */
  next_open: string;
  inputs_hash: string;
  code_version: string;
  /** Hours from the decision to that open; negative means it was recorded after the open (late). */
  lead_hours: number;
}

/** Forward edge Sharpe against the card's research band (annual values). */
export interface PaperBand {
  research: number | null;
  forward: number | null;
  floor: number | null;
  ceiling: number | null;
  status: 'inside' | 'below' | 'above' | null;
  /** Forward days before the band's floor clears zero, if the research edge is the truth. */
  evidence_days: number | null;
  evidence_years: number | null;
}

/** CUSUM decay alarm state. */
export interface PaperAlarm {
  applicable: boolean;
  reason?: string;
  k?: number;
  h?: number;
  arl0_days?: number;
  arl1_days?: number;
  value?: number;
  alarm: boolean;
  first_alarm: string | null;
}

/** One row of the book table: an entry with its latest decision and forward headline numbers. */
export interface PaperEntry {
  entry_id: string;
  card_id: string;
  kind: 'bars' | 'portfolio';
  question: string;
  family: string;
  strategy: string;
  scope: string;
  params: Record<string, number>;
  verdict: Verdict;
  added_at: string;
  status: 'active' | 'retired';
  note: string;
  retired_at: string | null;
  research_edge_sharpe: number | null;
  n_decisions: number;
  last_decision: PaperDecision | null;
  late: number;
  scored_at: string | null;
  through: string | null;
  n_days: number;
  days_in_book: number | null;
  forward_return: number | null;
  benchmark_return: number | null;
  band: PaperBand | null;
  alarm: PaperAlarm | null;
}

/** The paper book and the cards that could join it. */
export interface PaperBook {
  entries: PaperEntry[];
  eligible: CardRow[];
}

/** Latest forward report of an entry, saved by each scoring. */
export interface PaperReport {
  scored_at: string;
  days_in_book: number;
  through: string | null;
  n_days: number;
  reference: { edge_sharpe: number | null; beta: number | null; research: [string, string]; n_days: number; trial_id: string };
  decisions: { n: number; late: number; last: PaperDecision | null; rows: PaperDecision[] };
  stats: { strategy: Record<string, number>; benchmark: Record<string, number> } | null;
  band: PaperBand;
  alarm: PaperAlarm | null;
  series: {
    time: number[];
    equity: number[];
    benchmark: number[];
    expected: number[];
    expected_lo: number[];
    expected_hi: number[];
    edge: number[];
    edge_expected: number[];
    edge_lo: number[];
    edge_hi: number[];
    cusum: number[];
    exposure: number[];
  } | null;
  trial_id: string | null;
}

/** One entry's page: its row, every decision (newest first) and the latest report. */
export interface PaperEntryDetail {
  entry: PaperEntry;
  decisions: PaperDecision[];
  report: PaperReport | null;
}

/** Result of the daily run for one entry. */
export interface PaperRunResult {
  entry_id: string;
  question: string;
  status: 'decided' | 'exists' | 'refused' | 'error';
  as_of: string | null;
  message: string;
  late?: boolean;
}

/** Result of scoring one entry. */
export interface PaperScoreResult {
  entry_id: string;
  question: string;
  status: 'scored' | 'error';
  through?: string | null;
  n_days?: number;
  alarm?: boolean | null;
  message: string;
}
