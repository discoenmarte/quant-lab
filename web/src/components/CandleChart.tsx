import { useEffect, useRef } from 'react';
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  createChart,
  createSeriesMarkers,
  type SeriesMarker,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { BarPoint } from '../api';

export interface ChartMarker {
  /** Bar time, Unix seconds. */
  time: number;
  /** Where to draw relative to the bar. */
  position: 'aboveBar' | 'belowBar' | 'inBar';
  /** Marker colour. */
  color: string;
  /** Marker shape. */
  shape: 'arrowUp' | 'arrowDown' | 'circle' | 'square';
  /** Short label. */
  text?: string;
}

/**
 * Candlestick chart with volume, built on TradingView Lightweight Charts (Apache-2.0; the attribution logo is
 * required by its licence and stays on).
 *
 * @param props.bars - OHLCV bars in time order.
 * @param props.markers - Optional markers such as trades, dividends or splits.
 * @param props.intraday - Show times on the axis, for sub-daily bars.
 * @param props.height - Height in pixels.
 */
export function CandleChart({
  bars,
  markers = [],
  intraday = false,
  height = 380,
}: {
  bars: BarPoint[];
  markers?: ChartMarker[];
  intraday?: boolean;
  height?: number;
}) {
  const el = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!el.current) return;
    const chart = createChart(el.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#8a97a8',
        fontFamily: 'Inter, system-ui, sans-serif',
        attributionLogo: true,
      },
      grid: { vertLines: { color: '#151c26' }, horzLines: { color: '#151c26' } },
      rightPriceScale: { borderColor: '#1d2633' },
      timeScale: { borderColor: '#1d2633', timeVisible: intraday, secondsVisible: false },
    });

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: '#34d399',
      downColor: '#f87171',
      borderVisible: false,
      wickUpColor: '#34d399',
      wickDownColor: '#f87171',
    });
    candles.setData(
      bars.map((b) => ({ time: b.time as UTCTimestamp, open: b.open, high: b.high, low: b.low, close: b.close })),
    );

    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: '' });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volume.setData(
      bars.map((b) => ({
        time: b.time as UTCTimestamp,
        value: b.volume,
        color: b.close >= b.open ? 'rgba(52, 211, 153, 0.3)' : 'rgba(248, 113, 113, 0.3)',
      })),
    );

    if (markers.length) {
      const sorted = [...markers].sort((a, b) => a.time - b.time);
      createSeriesMarkers(
        candles,
        sorted.map((m) => ({ ...m, time: m.time as UTCTimestamp }) as SeriesMarker<UTCTimestamp>),
      );
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, markers, intraday]);

  return <div ref={el} style={{ height, width: '100%' }} />;
}
