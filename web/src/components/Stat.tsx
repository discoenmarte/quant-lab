import type { ReactNode } from 'react';
import { InfoTip } from './InfoTip';

/**
 * Metric tile: a label, a large value and an optional line of context.
 *
 * @param props.label - Text above the value.
 * @param props.value - Already formatted value.
 * @param props.sub - Smaller context line under the value.
 * @param props.tone - `"up"` / `"down"` colour the value; `""` keeps it neutral.
 * @param props.term - Glossary key for the info tooltip.
 */
export function Stat({
  label,
  value,
  sub,
  tone = '',
  term,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: 'up' | 'down' | 'warn' | '';
  term?: string;
}) {
  return (
    <div className="stat">
      <div className="stat-label">
        {label}
        {term && <InfoTip term={term} />}
      </div>
      <div className={`stat-value num ${tone}`}>{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}
