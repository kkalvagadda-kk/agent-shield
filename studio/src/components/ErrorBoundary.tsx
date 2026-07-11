import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  // Optional replacement for the default full-screen error UI. Use `fallback={null}`
  // for infrastructure like the toast sink, where a crash should degrade silently
  // rather than blank/replace the whole app.
  fallback?: ReactNode;
}

interface State {
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("React ErrorBoundary caught:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      if (this.props.fallback !== undefined) {
        return this.props.fallback;
      }
      return (
        <div className="flex items-center justify-center min-h-screen bg-slate-50 p-8">
          <div className="max-w-lg w-full bg-white rounded-lg shadow-sm border border-red-200 p-6">
            <h2 className="text-lg font-semibold text-red-700 mb-2">Something went wrong</h2>
            <pre className="text-xs text-red-600 bg-red-50 rounded p-3 overflow-auto max-h-48 whitespace-pre-wrap">
              {this.state.error.message}
            </pre>
            <button
              onClick={() => this.setState({ error: null })}
              className="mt-4 px-4 py-2 text-sm font-medium text-white bg-slate-700 rounded hover:bg-slate-800"
            >
              Try Again
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
