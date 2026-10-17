import { useMemo, useState, type ReactNode } from 'react';

export interface Column<T> {
  /** Unique column id. */
  key: string;
  /** Header text. */
  label: ReactNode;
  /** Cell renderer; defaults to `row[key]`. */
  render?: (row: T) => ReactNode;
  /** Value used for sorting; columns without it can't be sorted. */
  sort?: (row: T) => number | string | null;
  /** Right-align numbers. */
  align?: 'left' | 'right';
}

/**
 * Plain, dense table with click-to-sort headers.
 *
 * @param props.columns - Column definitions.
 * @param props.rows - Data rows.
 * @param props.rowKey - Stable key for each row.
 * @param props.empty - Content shown when there are no rows.
 * @param props.maxHeight - Scroll inside the table above this height (pixels).
 * @param props.onRowClick - Called with the clicked row.
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  empty = 'Nothing yet.',
  maxHeight,
  onRowClick,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T, i: number) => string;
  empty?: ReactNode;
  maxHeight?: number;
  onRowClick?: (row: T) => void;
}) {
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [desc, setDesc] = useState(true);

  const sorted = useMemo(() => {
    const col = columns.find((c) => c.key === sortKey);
    if (!col?.sort) return rows;
    const get = col.sort;
    return [...rows].sort((a, b) => {
      const va = get(a);
      const vb = get(b);
      if (va === vb) return 0;
      if (va === null) return 1;
      if (vb === null) return -1;
      return (va < vb ? -1 : 1) * (desc ? -1 : 1);
    });
  }, [rows, columns, sortKey, desc]);

  if (!rows.length) return <div className="empty">{empty}</div>;

  return (
    <div className="table-wrap" style={maxHeight ? { maxHeight } : undefined}>
      <table className="table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                className={`${c.align === 'right' ? 'right' : ''} ${c.sort ? 'sortable' : ''}`}
                onClick={() => {
                  if (!c.sort) return;
                  if (sortKey === c.key) setDesc(!desc);
                  else {
                    setSortKey(c.key);
                    setDesc(true);
                  }
                }}
              >
                {c.label}
                {sortKey === c.key ? (desc ? ' ↓' : ' ↑') : ''}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => (
            <tr
              key={rowKey(row, i)}
              className={onRowClick ? 'clickable' : ''}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              {columns.map((c) => (
                <td key={c.key} className={c.align === 'right' ? 'right num' : ''}>
                  {c.render ? c.render(row) : String((row as Record<string, unknown>)[c.key] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
