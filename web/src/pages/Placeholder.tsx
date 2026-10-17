import { Badge } from '../components/Badge';
import { Card } from '../components/Card';
import type { NavItem } from '../nav';

/**
 * Stand-in for a page whose milestone isn't built yet. Shows what the page will contain.
 *
 * @param props.item - The navigation entry for the page.
 */
export function Placeholder({ item }: { item: NavItem }) {
  return (
    <div className="page">
      <div className="page-head">
        <h1>{item.label}</h1>
        <Badge tone="muted">arrives in {item.milestone}</Badge>
      </div>
      <p className="lead">{item.blurb}</p>
      {item.planned && (
        <Card title="What this page will show">
          <ul className="plain-list">
            {item.planned.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
