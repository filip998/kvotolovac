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
    <div className="flex min-h-screen flex-col">
      {/* Header */}
      <header className="border-b border-gray-800 bg-gray-950">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <Link to="/" className="flex items-center gap-2">
            <span className="text-2xl">🎯</span>
            <div>
              <h1 className="text-lg font-bold leading-tight text-white">KvotoLovac</h1>
              <p className="text-[10px] font-medium uppercase tracking-widest text-brand-400">
                Odds Hunter
              </p>
            </div>
          </Link>

          <nav className="flex items-center gap-1">
            {navLinks.map((link) => (
              <Link
                key={link.to}
                to={link.to}
                className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                  location.pathname === link.to
                    ? 'bg-gray-800 text-white'
                    : 'text-gray-400 hover:bg-gray-800/50 hover:text-gray-200'
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

      {/* Main Content */}
      <main className="flex-1">
        <div className="mx-auto max-w-6xl px-4 py-6">
          <ErrorBoundary>
            <Outlet />
          </ErrorBoundary>
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t border-gray-800 px-4 py-4 text-center text-xs text-gray-600">
        KvotoLovac 🎯 — Odds comparison for Serbian bookmakers
      </footer>
    </div>
  );
}
