import { Component, type ReactNode, type ErrorInfo } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('ErrorBoundary caught:', error, info);
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="mx-auto max-w-md rounded-lg border border-danger/30 bg-danger/10 p-8 text-center">
          <h2 className="mb-2 text-base font-semibold text-danger">Something went wrong</h2>
          <p className="mb-4 text-sm text-text-secondary">
            {this.state.error?.message || 'An unexpected error occurred.'}
          </p>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            className="rounded-md bg-danger/20 px-4 py-2 text-sm font-medium text-danger transition hover:bg-danger/30"
          >
            Try again
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
