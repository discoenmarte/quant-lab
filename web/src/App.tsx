import { useMemo, useState, type ReactElement } from 'react';
import { NavLink, Route, Routes, useNavigate } from 'react-router-dom';
import { post, type DemoResult } from './api';
import { Clocks } from './components/Clocks';
import { CommandPalette, type Command } from './components/CommandPalette';
import { Icon } from './components/Icon';
import { ToastProvider, useToast } from './components/Toast';
import { GlossaryProvider } from './glossary';
import { useApi } from './hooks';
import { NAV, NAV_ITEMS } from './nav';
import { Audit } from './pages/Audit';
import { BacktestStudio } from './pages/BacktestStudio';
import { DataHealth } from './pages/DataHealth';
import { Gauntlet } from './pages/Gauntlet';
import { Glossary } from './pages/Glossary';
import { Home } from './pages/Home';
import { Ledger } from './pages/Ledger';
import { PortfolioLab } from './pages/PortfolioLab';
import { Placeholder } from './pages/Placeholder';
import { System } from './pages/System';
import { TradingDesk } from './pages/TradingDesk';

/** Built pages by route. Routes without an entry render their milestone placeholder. */
const PAGES: Record<string, ReactElement> = {
  '/': <Home />,
  '/data': <DataHealth />,
  '/backtest': <BacktestStudio />,
  '/portfolio': <PortfolioLab />,
  '/gauntlet': <Gauntlet />,
  '/ledger': <Ledger />,
  '/desk': <TradingDesk />,
  '/system': <System />,
  '/audit': <Audit />,
  '/glossary': <Glossary />,
};

/**
 * Top bar: research-mode banner, clocks, API status and the command palette button.
 *
 * @param props.onPalette - Opens the command palette.
 */
function TopBar({ onPalette }: { onPalette: () => void }) {
  const { data, error } = useApi<{ ok: boolean; version: string }>('/health', 15000);
  return (
    <header className="topbar">
      <div className="mode">
        <span className="mode-dot" />
        RESEARCH <span className="muted">· paper only, no real money</span>
      </div>
      <Clocks />
      <div className="topbar-right">
        <span
          className={`api-dot ${error ? 'bad' : data?.ok ? 'ok' : ''}`}
          title={error ? `API unreachable: ${error}` : 'API connected'}
        />
        <span className="muted">API {data?.version ?? ''}</span>
        <button className="btn ghost" onClick={onPalette}>
          <Icon name="search" /> Commands <kbd>⌘K</kbd>
        </button>
      </div>
    </header>
  );
}

/** Sidebar with every page, grouped, each tagged with the milestone that delivers it. */
function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="brand">
        <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden="true">
          <rect width="32" height="32" rx="7" fill="#0f172a" />
          <path d="M7 22l6-7 5 4 7-10" stroke="#38bdf8" strokeWidth="3" fill="none" strokeLinecap="round" />
        </svg>
        quant-lab <small>v0.1</small>
      </div>
      {NAV.map((group) => (
        <nav key={group.group}>
          <div className="nav-group">{group.group}</div>
          {group.items.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === '/'}
              className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
            >
              <Icon name={item.icon} />
              {item.label}
              {!item.ready && <span className="tag">{item.milestone}</span>}
            </NavLink>
          ))}
        </nav>
      ))}
    </aside>
  );
}

/** Layout, routes and the command palette. */
function Shell() {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const navigate = useNavigate();
  const notify = useToast();

  const commands = useMemo<Command[]>(
    () => [
      ...NAV_ITEMS.map((item) => ({
        id: `go:${item.path}`,
        group: 'Go to',
        label: item.label,
        hint: item.ready ? item.milestone : `${item.milestone} · coming`,
        run: () => navigate(item.path),
      })),
      {
        id: 'run:demo',
        group: 'Run',
        label: 'Demo pipeline',
        hint: 'records one trial',
        run: async () => {
          try {
            const r = await post<DemoResult>('/demo');
            notify(`Trial ${r.trial_id} recorded. N = ${r.n_trials} on ${r.question}.`, 'ok');
          } catch (err) {
            notify(`Demo failed: ${String(err)}`, 'bad');
          }
        },
      },
      { id: 'open:docs', group: 'Open', label: 'API documentation', run: () => window.open('/docs', '_blank') },
    ],
    [navigate, notify],
  );

  return (
    <div className="shell">
      <Sidebar />
      <div className="main">
        <TopBar onPalette={() => setPaletteOpen(true)} />
        <main className="content">
          <Routes>
            {NAV_ITEMS.map((item) => (
              <Route
                key={item.path}
                path={item.path}
                element={item.ready && PAGES[item.path] ? PAGES[item.path] : <Placeholder item={item} />}
              />
            ))}
            <Route path="*" element={<div className="page"><h1>Not found</h1></div>} />
          </Routes>
        </main>
      </div>
      <CommandPalette commands={commands} open={paletteOpen} setOpen={setPaletteOpen} />
    </div>
  );
}

/** Root component: glossary and notifications wrap the shell. */
export default function App() {
  return (
    <GlossaryProvider>
      <ToastProvider>
        <Shell />
      </ToastProvider>
    </GlossaryProvider>
  );
}
