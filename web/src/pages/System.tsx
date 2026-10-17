import type { SystemInfo } from '../api';
import { Card } from '../components/Card';
import { int, num } from '../format';
import { useApi } from '../hooks';

/** Versions, paths, database size, record counts and links to companion services. */
export function System() {
  const { data, error } = useApi<SystemInfo>('/system', 30000);
  if (error) return <div className="page"><div className="empty">API unreachable: {error}</div></div>;
  if (!data) return <div className="page"><div className="empty">Loading…</div></div>;
  return (
    <div className="page">
      <div className="page-head">
        <h1>System</h1>
        <span className="muted">quant-lab {data.version}</span>
      </div>
      <div className="grid cols-2">
        <Card title="Runtime">
          <dl className="kv">
            <dt>Python</dt>
            <dd className="num">{data.python}</dd>
            <dt>Platform</dt>
            <dd>{data.platform}</dd>
            {Object.entries(data.libraries).map(([k, v]) => (
              <div key={k} style={{ display: 'contents' }}>
                <dt>{k}</dt>
                <dd className="num">{v}</dd>
              </div>
            ))}
          </dl>
        </Card>
        <Card title="Storage">
          <dl className="kv">
            <dt>Data folder</dt>
            <dd className="num">{data.paths.data}</dd>
            <dt>Database</dt>
            <dd className="num">{data.paths.database}</dd>
            <dt>Database size</dt>
            <dd className="num">{num(data.database_mb)} MB</dd>
          </dl>
        </Card>
        <Card title="Records">
          <dl className="kv">
            {Object.entries(data.counts).map(([k, v]) => (
              <div key={k} style={{ display: 'contents' }}>
                <dt>{k.replace('_', ' ')}</dt>
                <dd className="num">{int(v)}</dd>
              </div>
            ))}
          </dl>
        </Card>
        <Card title="Services">
          <ul className="plain-list">
            {data.services.map((s) => (
              <li key={s.name}>
                <a href={s.url} target="_blank" rel="noreferrer">
                  {s.name}
                </a>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}
