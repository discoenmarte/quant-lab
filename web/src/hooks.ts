import { useCallback, useEffect, useState } from 'react';
import { get } from './api';

/**
 * Load a Lab API resource and keep its loading and error state.
 *
 * @param path - Path below `/api`, or null to skip loading.
 * @param refreshMs - Re-fetch interval in milliseconds; 0 disables polling.
 * @returns `data`, `error`, `loading` and a `reload` function.
 */
export function useApi<T>(path: string | null, refreshMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const reload = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try {
      setData(await get<T>(path));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    void reload();
    if (!refreshMs) return;
    const id = window.setInterval(() => void reload(), refreshMs);
    return () => window.clearInterval(id);
  }, [reload, refreshMs]);

  return { data, error, loading, reload };
}
