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
      <header className="sticky top-0 z-40 border-b border-border bg-bg/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-7xl items-center justify-between gap-4 px-5 py-3 sm:px-6">
          <Link to="/" className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-accent font-display text-sm font-bold text-bg">
              K
            </div>
            <span className="font-display text-base font-semibold text-text">KvotoLovac</span>
          </Link>

          <nav className="flex items-center gap-1">
            {navLinks.map((link) => (
              <Link
                key={link.to}
                to={link.to}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                  location.pathname === link.to
                    ? 'bg-surface-raised text-text'
                    : 'text-text-secondary hover:text-text'
                }`}
              >
                {link.label}
              </Link>
            ))}
          </nav>
        </div>
      </header>

      <StatusBar />

      <main className="flex-1">
        <div className="mx-auto w-full max-w-7xl px-5 py-8 sm:px-6">
          <ErrorBoundary>
            <Outlet />
          </ErrorBoundary>
        </div>
      </main>

      <footer className="border-t border-border px-5 py-4 text-center text-xs text-text-muted">
        KvotoLovac — Basketball odds comparison for Serbian bookmakers
      </footer>
    </div>
  );
}
