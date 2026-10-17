import type { ReactNode } from 'react';

export type BadgeTone = 'ok' | 'warn' | 'bad' | 'info' | 'muted';

/**
 * Compact status label. Colour carries meaning: ok = green, warn = amber, bad = red.
 *
 * @param props.tone - Status colour.
 * @param props.children - Label text.
 */
export function Badge({ tone = 'muted', children }: { tone?: BadgeTone; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
