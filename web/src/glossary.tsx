import { createContext, useContext, type ReactNode } from 'react';
import type { GlossaryEntry } from './api';
import { useApi } from './hooks';

const GlossaryContext = createContext<Record<string, GlossaryEntry>>({});

/**
 * Load the glossary once and make it available to every info tooltip.
 *
 * @param props.children - The app.
 */
export function GlossaryProvider({ children }: { children: ReactNode }) {
  const { data } = useApi<Record<string, GlossaryEntry>>('/glossary');
  return <GlossaryContext.Provider value={data ?? {}}>{children}</GlossaryContext.Provider>;
}

/**
 * Look up glossary entries loaded by `GlossaryProvider`.
 *
 * @returns Mapping of glossary key to entry; empty until loaded.
 */
export function useGlossary(): Record<string, GlossaryEntry> {
  return useContext(GlossaryContext);
}
