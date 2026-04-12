import { Link, Outlet, useLocation } from 'react-router-dom';
import StatusBar from './StatusBar';
import ErrorBoundary from './ErrorBoundary';
import { useTheme } from '../hooks/useTheme';

const navLinks = [
  { to: '/', label: 'Dashboard' },
  { to: '/about', label: 'About' },
];

export default function Layout() {
  const location = useLocation();
  const { theme, toggle } = useTheme();

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

          <div className="flex items-center gap-2">
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
            <button
              onClick={toggle}
              aria-label="Toggle theme"
              aria-pressed={theme === 'light'}
              className="rounded-md p-1.5 text-text-muted transition hover:text-text"
            >
              {theme === 'dark' ? (
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="5" />
                  <line x1="12" y1="1" x2="12" y2="3" />
                  <line x1="12" y1="21" x2="12" y2="23" />
                  <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
                  <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
                  <line x1="1" y1="12" x2="3" y2="12" />
                  <line x1="21" y1="12" x2="23" y2="12" />
                  <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
                  <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
                </svg>
              ) : (
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
                </svg>
              )}
            </button>
          </div>
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
