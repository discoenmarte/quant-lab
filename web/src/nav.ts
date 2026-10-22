import type { IconName } from './components/Icon';

export interface NavItem {
  /** Route path. */
  path: string;
  /** Sidebar label. */
  label: string;
  /** Sidebar icon. */
  icon: IconName;
  /** Milestone that delivers the page. */
  milestone: string;
  /** Whether the page is built. Unbuilt pages show what's coming. */
  ready: boolean;
  /** One-line description. */
  blurb: string;
  /** What the page will show, for pages not built yet. */
  planned?: string[];
}

/** Sidebar structure. Each page is tied to the milestone that delivers it. */
export const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: 'Lab',
    items: [
      { path: '/', label: 'Overview', icon: 'home', milestone: 'M0', ready: true, blurb: 'Milestones, counts and the demo pipeline.' },
    ],
  },
  {
    group: 'Data',
    items: [
      {
        path: '/data',
        label: 'Data Health',
        icon: 'data',
        milestone: 'M1',
        ready: true,
        blurb: 'Every source, how fresh it is, and what looks wrong.',
        planned: [
          'Freshness board per source',
          'Coverage heatmap (symbol × month)',
          'Candle inspector with split and dividend markers',
          'Data-issues grid, re-ingest and backfill buttons',
        ],
      },
    ],
  },
  {
    group: 'Research',
    items: [
      {
        path: '/backtest',
        label: 'Backtest Studio',
        icon: 'flask',
        milestone: 'M2',
        ready: true,
        blurb: 'Run a strategy with realistic costs and read the full report.',
        planned: [
          'Stats banner with the trial count next to every Sharpe',
          'Equity vs benchmark, underwater drawdown, exposure',
          'Monthly-returns heatmap and cost breakdown',
          'Trades plotted on the candle chart',
        ],
      },
      {
        path: '/portfolio',
        label: 'Portfolio Lab',
        icon: 'layers',
        milestone: 'M3b',
        ready: true,
        blurb: 'Diversified rules across asset classes, judged against equal weight.',
        planned: ['Weights over time', 'Equity against equal weight', 'Contributions per instrument'],
      },
      {
        path: '/gauntlet',
        label: 'Gauntlet',
        icon: 'shield',
        milestone: 'M3',
        ready: true,
        blurb: 'Ten tests a strategy must pass before paper trading.',
        planned: [
          'Report card with ten stage badges',
          'Permutation histogram: your result vs noise',
          'Plateau heatmap, Monte Carlo fan, DSR and PBO gauges',
          'Calibration: false-positive and power curves',
        ],
      },
      {
        path: '/ledger',
        label: 'Ledger',
        icon: 'list',
        milestone: 'M3',
        ready: true,
        blurb: 'Every trial ever run, grouped by research question.',
        planned: ['All trials with filters', 'N per research question', 'Best Sharpe next to its trial count'],
      },
      {
        path: '/inbox',
        label: 'Research Inbox',
        icon: 'inbox',
        milestone: 'M4',
        ready: true,
        blurb: 'New papers, scored and turned into testable specs by a local model.',
        planned: ['Full-text reading of the best papers (PDF)', 'New strategy templates for papers no template fits'],
      },
      {
        path: '/mining',
        label: 'Mining Lab',
        icon: 'cpu',
        milestone: 'M5',
        ready: true,
        blurb: 'Budgeted strategy search over a grammar, every try a trial, judged by the same gauntlet.',
        planned: ['Nightly mining on the Mac', 'Formula-tree viewer', 'Embedded Optuna dashboard'],
      },
    ],
  },
  {
    group: 'Operations',
    items: [
      {
        path: '/board',
        label: 'Strategy Board',
        icon: 'board',
        milestone: 'M6',
        ready: true,
        blurb: 'Every report card in its lifecycle state, the daily lab report and the alert log.',
        planned: ['Allocator across validated strategies (HRP, risk parity with caps)', 'Correlation heatmap and "what if I add X?"'],
      },
      {
        path: '/desk',
        label: 'Trading Desk',
        icon: 'desk',
        milestone: 'M7b',
        ready: true,
        blurb: 'Forward paper book, and its decisions traded on paper broker accounts with a kill switch.',
        planned: ['Live price feed for crypto decisions'],
      },
      { path: '/gate', label: 'Go-Live Gate', icon: 'gate', milestone: 'M8', ready: true, blurb: 'The evidence checklist before any real money, item by item.' },
      { path: '/intraday', label: 'Intraday Cockpit', icon: 'clock', milestone: 'M9', ready: false, blurb: 'Pre-market briefing and replay.' },
    ],
  },
  {
    group: 'System',
    items: [
      { path: '/system', label: 'System', icon: 'server', milestone: 'M0', ready: true, blurb: 'Versions, paths and services.' },
      { path: '/audit', label: 'Audit Log', icon: 'audit', milestone: 'M0', ready: true, blurb: 'Every action: who, what, when, why.' },
      { path: '/glossary', label: 'Glossary', icon: 'book', milestone: 'M0', ready: true, blurb: 'Every metric in plain words.' },
    ],
  },
];

/** All nav items in sidebar order. */
export const NAV_ITEMS: NavItem[] = NAV.flatMap((g) => g.items);
