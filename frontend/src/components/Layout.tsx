import { Link, Outlet, useLocation } from 'react-router-dom';
import StatusBar from './StatusBar';
import ErrorBoundary from './ErrorBoundary';

const navLinks = [
  { to: '/', label: 'Dashboard' },
  { to: '/about', label: 'About' },
];

export default function Layout() {
  const location = useLocation();

  return (
    <div className="relative flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 border-b border-line-700/70 bg-ink-950/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-7xl items-center justify-between gap-4 px-4 py-4 sm:px-6">
          <Link to="/" className="flex min-w-0 items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-white text-base font-semibold text-black">
              K
            </div>
            <div className="min-w-0">
              <div className="font-display text-lg font-semibold text-white">KvotoLovac</div>
              <p className="text-xs text-slate-400">Odds Hunter</p>
            </div>
          </Link>

          <nav className="flex items-center gap-1 rounded-xl border border-line-700/80 bg-ink-900 p-1">
            {navLinks.map((link) => (
              <Link
                key={link.to}
                to={link.to}
                className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
                  location.pathname === link.to
                    ? 'bg-ink-750 text-white'
                    : 'text-slate-400 hover:bg-ink-850 hover:text-slate-100'
                }`}
              >
                {link.label}
              </Link>
            ))}
          </nav>
        </div>
      </header>

      {/* Status Bar */}
      <StatusBar />

      <main className="flex-1">
        <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6">
          <ErrorBoundary>
            <Outlet />
          </ErrorBoundary>
        </div>
      </main>

      <footer className="border-t border-line-700/60 bg-ink-950 px-4 py-5 text-center text-xs text-slate-500">
        KvotoLovac - Basketball odds comparison for Serbian bookmakers
      </footer>
    </div>
  );
}
