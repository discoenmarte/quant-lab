/**
 * Chart options shared by several pages. Colours follow the design system: grey axes, red for losses,
 * green for gains.
 */
import type { EChartsOption } from 'echarts';

export const AXIS = '#8a97a8';
export const GRID_LINE = '#151c26';
export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** One calendar month's compounded return. */
export interface MonthlyReturn {
  year: number;
  month: number;
  ret: number;
}

/**
 * Monthly returns heatmap: one row per year, one column per month, red for losses and green for gains.
 *
 * @param monthly - Compounded return per calendar month.
 * @returns ECharts option.
 */
export function monthlyOption(monthly: MonthlyReturn[]): EChartsOption {
  const years = [...new Set(monthly.map((m) => m.year))].sort((a, b) => b - a);
  const data = monthly.map((m) => [m.month - 1, years.indexOf(m.year), m.ret]);
  return {
    tooltip: {
      formatter: (p: unknown) => {
        const v = (p as { value: number[] }).value;
        return `${MONTHS[v[0]]} ${years[v[1]]}: ${(v[2] * 100).toFixed(1)}%`;
      },
    },
    grid: { left: 44, right: 8, top: 4, bottom: 22 },
    xAxis: { type: 'category', data: MONTHS, axisLabel: { color: AXIS, fontSize: 10 } },
    yAxis: { type: 'category', data: years.map(String), axisLabel: { color: AXIS, fontSize: 10 } },
    visualMap: { min: -0.1, max: 0.1, show: false, inRange: { color: ['#b91c1c', '#3b1d1d', '#141a24', '#0f3b2e', '#059669'] } },
    series: [{ type: 'heatmap', data, itemStyle: { borderColor: '#0b0f14', borderWidth: 1 } }],
  };
}

/**
 * Height for a monthly heatmap with one row per year.
 *
 * @param monthly - Monthly returns.
 * @returns Pixels, at most 560.
 */
export function monthlyHeight(monthly: MonthlyReturn[]): number {
  return Math.min(560, new Set(monthly.map((m) => m.year)).size * 15 + 40);
}
