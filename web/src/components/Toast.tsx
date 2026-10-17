import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';

type Tone = 'ok' | 'warn' | 'bad' | 'info';
interface ToastItem {
  id: number;
  text: string;
  tone: Tone;
}

const ToastContext = createContext<(text: string, tone?: Tone) => void>(() => {});

/**
 * Hosts transient notifications in the bottom-right corner. Each disappears after five seconds.
 *
 * @param props.children - The app.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const notify = useCallback((text: string, tone: Tone = 'info') => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, text, tone }]);
    window.setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 5000);
  }, []);

  return (
    <ToastContext.Provider value={notify}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast toast-${t.tone}`}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/**
 * Get the function that shows a notification.
 *
 * @returns `notify(text, tone)`.
 */
export function useToast() {
  return useContext(ToastContext);
}
