import { useEffect, useRef } from 'react';
import * as echarts from 'echarts';
import type { EChartsOption } from 'echarts';

/**
 * Apache ECharts canvas that follows its container's size.
 *
 * @param props.option - Chart definition; replaced (not merged) on every change.
 * @param props.height - Height in pixels; width fills the container.
 */
export function EChart({ option, height = 260 }: { option: EChartsOption; height?: number }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!el.current) return;
    chart.current = echarts.init(el.current, 'dark', { renderer: 'canvas' });
    const ro = new ResizeObserver(() => chart.current?.resize());
    ro.observe(el.current);
    return () => {
      ro.disconnect();
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(
      {
        backgroundColor: 'transparent',
        textStyle: { fontFamily: 'Inter, system-ui, sans-serif' },
        ...option,
      },
      true,
    );
  }, [option]);

  return <div ref={el} style={{ height, width: '100%' }} />;
}
