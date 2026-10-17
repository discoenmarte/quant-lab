import { useGlossary } from '../glossary';

/**
 * Small "i" marker that shows a glossary definition on hover.
 *
 * @param props.term - Glossary key, e.g. `"dsr"`. Renders nothing for unknown keys.
 */
export function InfoTip({ term }: { term: string }) {
  const entry = useGlossary()[term];
  if (!entry) return null;
  return (
    <span className="infotip" tabIndex={0} aria-label={entry.term}>
      i
      <span className="infotip-body" role="tooltip">
        <strong>{entry.term}</strong>
        <span>{entry.short}</span>
        <span className="muted">{entry.long}</span>
      </span>
    </span>
  );
}
