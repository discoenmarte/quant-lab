import type { ReactNode } from 'react';
import { InfoTip } from './InfoTip';

/**
 * Panel with an optional title row, glossary tooltip and actions.
 *
 * @param props.title - Heading text.
 * @param props.term - Glossary key for an info tooltip next to the title.
 * @param props.actions - Buttons or controls aligned right in the title row.
 * @param props.className - Extra classes, e.g. `"span-2"` to widen it in a grid.
 * @param props.children - Panel content.
 */
export function Card({
  title,
  term,
  actions,
  className = '',
  children,
}: {
  title?: ReactNode;
  term?: string;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <h3>
            {title}
            {term && <InfoTip term={term} />}
          </h3>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      <div className="card-body">{children}</div>
    </section>
  );
}
