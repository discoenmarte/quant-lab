/**
 * Types for the broker paper-trading endpoints (`/api/broker/*`). Kept apart from `api.ts` so the broker layer
 * can grow on its own.
 */

/** A row of the append-only control log that hasn't taken effect yet. */
export interface PendingControl {
  ts: string;
  action: 'kill' | 'pause' | 'resume' | 'limits';
  scope: string;
  reason: string;
  actor: string;
  effective_at: string;
}

/** Whether new orders are allowed, and what is waiting to take effect. */
export interface BrokerControls {
  mode: 'active' | 'paused' | 'killed';
  since: string | null;
  reason: string | null;
  /** Entry id to `paused` or `active`, for entries with their own pause or resume. */
  entries: Record<string, 'paused' | 'active'>;
  pending: PendingControl[];
  limits: Record<string, number>;
  /** Active sleeves (only from `/broker/controls`). */
  sleeves?: number;
}

/** One paper account as the Trading Desk shows it. */
export interface BrokerAccount {
  configured: boolean;
  quote: string;
  account?: { equity: number; cash: number; currency: string; tradable: boolean };
  holdings?: Record<string, number>;
  error?: string;
  reconciliation: { ts: string; ok: boolean; details: Record<string, { expected: number; actual: number }> } | null;
}

/** A paper-book entry's slice of a paper account. */
export interface Sleeve {
  entry_id: string;
  broker: string;
  capital: number;
  status: string;
  enrolled_at: string;
  reason: string;
  holdings: Record<string, number>;
  value: Record<string, number>;
  cash: number;
  equity: number;
  return: number;
  state: 'paused' | 'active';
  last_decision: { as_of: string; decided_at: string; next_open: string; targets: Record<string, number> } | null;
}

/** An order the lab sent (or a limit stopped). */
export interface BrokerOrder {
  client_id: string;
  entry_id: string;
  broker: string;
  purpose: 'rebalance' | 'flatten';
  as_of: string | null;
  symbol: string;
  side: 'buy' | 'sell';
  qty: number;
  tif: string;
  ref_price: number | null;
  planned_at: string;
  submitted_at: string | null;
  late: boolean;
  status: string;
  filled_qty: number;
  avg_price: number | null;
  message: string;
}

/** One execution reported by a broker. */
export interface BrokerFill {
  fill_id: string;
  client_id: string;
  broker: string;
  symbol: string;
  side: 'buy' | 'sell';
  qty: number;
  price: number;
  fee: number;
  fee_asset: string;
  filled_at: string;
}

/** A filled rebalance against the open the engine would have used. */
export interface SlippageRow {
  client_id: string;
  entry_id: string;
  symbol: string;
  side: string;
  qty: number;
  tif: string;
  late: boolean;
  fill_price: number;
  model_price: number;
  slippage_bps: number;
  model_bps: number;
}

/** Everything `/api/broker/status` returns. */
export interface BrokerStatus {
  controls: BrokerControls;
  brokers: Record<string, BrokerAccount>;
  sleeves: Sleeve[];
  orders: BrokerOrder[];
  fills: BrokerFill[];
  slippage: { orders: SlippageRow[]; summary: Record<string, { n: number; mean_bps: number; median_bps: number }> };
}

/** One sleeve's result from `/api/broker/execute`. */
export interface ExecuteResult {
  entry_id: string;
  question?: string;
  status: 'sent' | 'exists' | 'blocked' | 'not_configured' | 'no_decision' | 'too_late' | 'error';
  as_of: string | null;
  orders: { client_id: string; symbol: string; side: string; qty: number; tif: string; status: string; message: string }[];
  notes: string[];
}

/** Plain-language names and units of the limits. */
export const LIMIT_LABELS: Record<string, string> = {
  sleeve_capital: 'Paper money per entry',
  max_order_notional: 'Largest single order',
  max_sleeve_gross: 'Holdings per sleeve (× its equity)',
  max_account_share: 'All sleeves (share of the account)',
  max_orders_per_day: 'Orders per day',
  min_order_notional: 'Smallest order (dust)',
};

/** Window event fired after any broker control action, so every view of the controls reloads at once. */
export const BROKER_CHANGED = 'qlab:broker-changed';
