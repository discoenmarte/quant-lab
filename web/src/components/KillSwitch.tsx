import { useEffect, useState } from 'react';
import { post } from '../api';
import { BROKER_CHANGED, type BrokerControls } from '../broker';
import { useApi } from '../hooks';
import { useToast } from './Toast';

/**
 * The always-visible kill switch in the top bar, with the paper-trading mode next to it.
 *
 * Reducing risk is instant: one click arms the button for four seconds and a second click stops all paper trading,
 * cancels open orders and sells every sleeve. There is no form to fill in. Lifting it takes a typed RESUME, a reason
 * and 24 hours, on the Trading Desk.
 */
export function KillSwitch() {
  const controls = useApi<BrokerControls>('/broker/controls', 15000);
  const notify = useToast();
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const mode = controls.data?.mode ?? 'active';
  const { reload } = controls;
  useEffect(() => {
    const onChange = () => void reload();
    window.addEventListener(BROKER_CHANGED, onChange);
    return () => window.removeEventListener(BROKER_CHANGED, onChange);
  }, [reload]);

  async function click() {
    if (!armed) {
      setArmed(true);
      window.setTimeout(() => setArmed(false), 4000);
      return;
    }
    setBusy(true);
    try {
      const out = await post<{ cancelled: string[]; flatten: Record<string, unknown[]> }>('/broker/kill', {
        reason: 'kill switch (top bar)',
      });
      const sells = Object.values(out.flatten).reduce((a, v) => a + v.length, 0);
      notify(`Paper trading killed: ${out.cancelled.length} open orders cancelled, ${sells} sell orders sent.`, 'warn');
      window.dispatchEvent(new Event(BROKER_CHANGED));
      await controls.reload();
    } catch (err) {
      notify(`Kill failed: ${String(err)}`, 'bad');
    } finally {
      setBusy(false);
      setArmed(false);
    }
  }

  const tone = mode === 'killed' ? 'bad' : mode === 'paused' ? 'warn' : 'ok';
  return (
    <span className="kill-wrap">
      <span className={`trade-mode ${tone}`} title={controls.data?.reason ?? 'Broker paper trading'}>
        {mode === 'active' ? `TRADING · ${controls.data?.sleeves ?? 0} sleeves` : mode.toUpperCase()}
      </span>
      <button className={`btn danger kill ${armed ? 'armed' : ''}`} onClick={click} disabled={busy || mode === 'killed'}
        title="Stop all paper trading, cancel open orders and sell every sleeve">
        {busy ? 'Killing…' : armed ? 'Click again to KILL' : 'KILL'}
      </button>
    </span>
  );
}
