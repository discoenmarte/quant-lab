import { useEffect, useMemo, useRef, useState } from 'react';
import { Icon } from './Icon';

export interface Command {
  /** Unique id. */
  id: string;
  /** What the person types to find it. */
  label: string;
  /** Group heading, e.g. "Go to" or "Run". */
  group: string;
  /** Short right-aligned hint, e.g. the milestone. */
  hint?: string;
  /** Action to perform. */
  run: () => void;
}

/**
 * Keyboard-driven command palette. Opens with ⌘K (Ctrl+K), filters as you type, runs on Enter.
 *
 * @param props.commands - Everything the palette can do.
 * @param props.open - Whether the palette is visible.
 * @param props.setOpen - Called to open or close it.
 */
export function CommandPalette({
  commands,
  open,
  setOpen,
}: {
  commands: Command[];
  open: boolean;
  setOpen: (v: boolean) => void;
}) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen(!open);
      } else if (e.key === 'Escape' && open) {
        setOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, setOpen]);

  useEffect(() => {
    if (open) {
      setQuery('');
      setActive(0);
      window.setTimeout(() => input.current?.focus(), 0);
    }
  }, [open]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    return commands.filter((c) => !q || `${c.group} ${c.label}`.toLowerCase().includes(q));
  }, [commands, query]);

  if (!open) return null;

  const runAt = (i: number) => {
    const cmd = matches[i];
    if (!cmd) return;
    setOpen(false);
    cmd.run();
  };

  return (
    <div className="palette-backdrop" onClick={() => setOpen(false)}>
      <div className="palette" role="dialog" aria-label="Command palette" onClick={(e) => e.stopPropagation()}>
        <div className="palette-input">
          <Icon name="search" />
          <input
            ref={input}
            value={query}
            placeholder="Type a command or a page…"
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') {
                e.preventDefault();
                setActive((a) => Math.min(a + 1, matches.length - 1));
              } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                setActive((a) => Math.max(a - 1, 0));
              } else if (e.key === 'Enter') {
                runAt(active);
              }
            }}
          />
        </div>
        <ul className="palette-list">
          {matches.map((c, i) => (
            <li
              key={c.id}
              className={i === active ? 'active' : ''}
              onMouseEnter={() => setActive(i)}
              onClick={() => runAt(i)}
            >
              <span className="palette-group">{c.group}</span>
              <span>{c.label}</span>
              {c.hint && <span className="palette-hint">{c.hint}</span>}
            </li>
          ))}
          {!matches.length && <li className="muted">No matching command.</li>}
        </ul>
      </div>
    </div>
  );
}
