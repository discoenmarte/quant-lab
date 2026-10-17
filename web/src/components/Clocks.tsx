import { useEffect, useState } from 'react';

const EXCHANGE_TZ = 'America/New_York';

/**
 * Format a moment as HH:MM:SS in a given timezone.
 *
 * @param d - The moment.
 * @param timeZone - IANA timezone name.
 * @returns 24-hour wall-clock time.
 */
function hms(d: Date, timeZone: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(d);
}

/**
 * Whether US equities are in regular trading hours (09:30-16:00 New York, Monday-Friday).
 * Exchange holidays are not checked.
 *
 * @param d - The moment.
 * @returns True during regular hours on a weekday.
 */
export function usMarketOpen(d: Date): boolean {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: EXCHANGE_TZ,
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '';
  const weekday = get('weekday');
  const minutes = Number(get('hour')) * 60 + Number(get('minute'));
  return !['Sat', 'Sun'].includes(weekday) && minutes >= 570 && minutes < 960;
}

/**
 * Live UTC and New York clocks with the US market's regular-hours status.
 * Storage is UTC everywhere; showing both side by side avoids timezone mix-ups.
 */
export function Clocks() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const open = usMarketOpen(now);
  return (
    <div className="clocks num">
      <span>
        UTC <b>{hms(now, 'UTC')}</b>
      </span>
      <span>
        New York <b>{hms(now, EXCHANGE_TZ)}</b>
      </span>
      <span className={open ? 'up' : 'faint'} title="Regular hours, weekdays; holidays not checked">
        US market {open ? 'open' : 'closed'}
      </span>
    </div>
  );
}
