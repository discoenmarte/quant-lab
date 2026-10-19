/**
 * Number and date formatting shared by every page. Missing values render as an en dash.
 */

const DASH = '–';

/**
 * Format a fraction as a percentage.
 *
 * @param x - Fraction, e.g. 0.123 for 12.3%.
 * @param digits - Decimal places.
 * @param signed - Prefix positive values with "+".
 * @returns The formatted string, or a dash for null/NaN.
 */
export function pct(x: number | null | undefined, digits = 1, signed = false): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return DASH;
  const s = `${(x * 100).toFixed(digits)}%`;
  return signed && x > 0 ? `+${s}` : s;
}

/**
 * Format a plain number.
 *
 * @param x - Value.
 * @param digits - Decimal places.
 * @returns The formatted string with thousands separators, or a dash for null/NaN.
 */
export function num(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return DASH;
  return x.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/**
 * Format an integer count.
 *
 * @param x - Value.
 * @returns The value with thousands separators, or a dash for null.
 */
export function int(x: number | null | undefined): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return DASH;
  return Math.round(x).toLocaleString('en-US');
}

/**
 * Format an ISO timestamp stored in UTC as "YYYY-MM-DD HH:MM UTC".
 *
 * @param iso - ISO-8601 string without timezone (the API stores naive UTC).
 * @returns The formatted time, or a dash when empty.
 */
export function utc(iso: string | null | undefined): string {
  if (!iso) return DASH;
  return `${iso.replace('T', ' ').slice(0, 16)} UTC`;
}

/**
 * Format a UTC timestamp as the exchange's wall-clock time, to show next to the UTC one.
 *
 * @param iso - ISO-8601 string in UTC without timezone (the API stores naive UTC).
 * @returns E.g. "Oct 18, 20:06 New York", or a dash when empty.
 */
export function nyTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(`${iso.slice(0, 19)}Z`);
  if (Number.isNaN(d.getTime())) return DASH;
  const text = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(d);
  return `${text} New York`;
}

/**
 * Pick a tone for a signed number, so gains read green and losses read red.
 *
 * @param x - Value.
 * @returns `"up"`, `"down"` or `""`.
 */
export function tone(x: number | null | undefined): 'up' | 'down' | '' {
  if (x === null || x === undefined || !Number.isFinite(x) || x === 0) return '';
  return x > 0 ? 'up' : 'down';
}
