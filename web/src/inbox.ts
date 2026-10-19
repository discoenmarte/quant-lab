/**
 * Types for the research inbox endpoints (`/api/inbox/*`). Kept apart from `api.ts` so the inbox can grow on its own.
 */

/** The seven rubric points, in display order, with plain-language labels. */
export const RUBRIC: { key: string; label: string }[] = [
  { key: 'testable', label: 'Runs on the lab’s data' },
  { key: 'specified', label: 'Specified precisely enough to code' },
  { key: 'out_of_sample', label: 'Out-of-sample or post-publication evidence' },
  { key: 'costs', label: 'Transaction costs accounted for' },
  { key: 'rationale', label: 'Economic reason it should work' },
  { key: 'breadth', label: 'Ten years or many instruments' },
  { key: 'search_disclosed', label: 'Says how many variants were tried' },
];

/** A paper with its latest rubric verdict. */
export interface InboxPaper {
  arxiv_id: string;
  title: string;
  abstract: string;
  authors: string[];
  categories: string[];
  primary_cat: string;
  published: string;
  url: string;
  pdf_url: string;
  score: number | null;
  is_strategy: boolean | null;
  dots: Record<string, number> | null;
  asset_class: string | null;
  frequency: string | null;
  one_line: string | null;
  why: string | null;
  model: string | null;
  triaged_at: string | null;
  n_specs: number;
}

/** What a spec says: a template, a target, parameters, and what the paper claims. */
export interface SpecBody {
  fit: 'exact' | 'approximate' | 'none';
  kind: 'bars' | 'portfolio';
  template: string;
  target: string;
  params: Record<string, number>;
  signal: string;
  rebalance: string;
  differences: string;
  claimed_sharpe: number | null;
  claimed_cagr: number | null;
  sample_start: string | null;
  sample_end: string | null;
  costs_included: boolean | null;
  rationale: string;
  notes: string[];
  published?: string;
}

/** One replication or post-publication run. */
export interface SpecRun {
  start?: string;
  end?: string;
  years?: number;
  sharpe?: number | null;
  edge_sharpe?: number | null;
  cagr?: number | null;
  max_dd?: number | null;
  trial_id?: string;
  error?: string;
  reason?: string;
}

/** A spec with its review state and results. */
export interface InboxSpec {
  spec_id: string;
  arxiv_id: string;
  created_at: string;
  model: string;
  spec: SpecBody;
  status: 'draft' | 'needs_code' | 'approved' | 'rejected' | 'replicated' | 'not_replicated' | 'judged';
  reviewed_at: string | null;
  review_reason: string | null;
  results: {
    in_sample: SpecRun;
    post_publication: SpecRun;
    verdict: 'replicated' | 'not_replicated' | 'no_claim';
    tolerance: number | null;
    claimed: Record<string, unknown>;
  } | null;
  card_id: string | null;
  title: string | null;
  url: string | null;
  abstract?: string;
}

/** A background inbox run. */
export interface InboxJob {
  id: string;
  status: 'running' | 'done' | 'failed';
  started_at: string;
  finished_at: string | null;
  log: string[];
  digest: string[] | null;
}

/** The model cost meter. */
export interface InboxUsage {
  by_provider: { provider: string; model: string; purpose: string; calls: number; ok: number; seconds: number;
    tokens_in: number; tokens_out: number; cost_usd: number }[];
  month: { cost_usd: number; budget_usd: number };
}

/** A saved digest. */
export interface InboxDigest {
  digest_id: string;
  created_at: string;
  headline: string[];
}

/** One spec's claimed, replicated and post-publication Sharpe ratios. */
export interface CompareRow {
  spec_id: string;
  title: string;
  template: string;
  target: string;
  claimed: number | null;
  replicated: number | null;
  post_publication: number | null;
  verdict: string;
}
