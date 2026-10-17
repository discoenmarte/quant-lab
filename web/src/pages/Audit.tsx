import type { AuditEvent } from '../api';
import { Badge } from '../components/Badge';
import { Card } from '../components/Card';
import { DataTable } from '../components/DataTable';
import { utc } from '../format';
import { useApi } from '../hooks';

/** Every action taken through the lab, newest first: who, what, when and why. */
export function Audit() {
  const { data } = useApi<AuditEvent[]>('/audit?limit=500', 15000);
  return (
    <div className="page">
      <div className="page-head">
        <h1>Audit log</h1>
        <span className="muted">Append-only record of every action.</span>
      </div>
      <Card>
        <DataTable
          rows={data ?? []}
          rowKey={(e) => String(e.id)}
          maxHeight={640}
          empty="No events yet."
          columns={[
            { key: 'ts', label: 'Time', sort: (e) => e.ts, render: (e) => <span className="num">{utc(e.ts)}</span> },
            { key: 'actor', label: 'Actor', sort: (e) => e.actor },
            { key: 'action', label: 'Action', sort: (e) => e.action, render: (e) => <Badge tone="info">{e.action}</Badge> },
            { key: 'target', label: 'Target', render: (e) => <span className="num">{e.target}</span> },
            { key: 'reason', label: 'Reason', render: (e) => e.reason || <span className="faint">–</span> },
            { key: 'details', label: 'Details', render: (e) => <span className="faint num">{e.details.slice(0, 80)}</span> },
          ]}
        />
      </Card>
    </div>
  );
}
