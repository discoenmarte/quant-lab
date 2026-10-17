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
