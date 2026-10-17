import { Card } from '../components/Card';
import { useGlossary } from '../glossary';

/** Every metric and test the lab uses, in plain words. The same text powers the "i" tooltips. */
export function Glossary() {
  const entries = Object.entries(useGlossary());
  return (
    <div className="page">
      <div className="page-head">
        <h1>Glossary</h1>
        <span className="muted">{entries.length} terms</span>
      </div>
      <div className="gloss">
        {entries.map(([key, e]) => (
          <Card key={key} title={e.term}>
            <p>{e.short}</p>
            <p className="muted">{e.long}</p>
          </Card>
        ))}
      </div>
    </div>
  );
}
