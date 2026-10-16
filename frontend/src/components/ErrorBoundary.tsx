/**
 * ErrorBoundary — a top-level guard against render-time exceptions.
 *
 * Without a boundary, a single throw during render (including during one of the
 * dashboard's 30s poll refetches) propagates to the root and React unmounts the
 * whole tree, leaving a blank page. Because the throw happens in the browser at
 * runtime, the Vite dev terminal shows nothing, which made the "frontend
 * disappears after a while, reload fixes it" symptom hard to trace.
 *
 * This boundary catches such errors and renders a small inline fallback instead
 * of blanking the app. The fallback offers two recovery paths:
 *   - "Try again" clears the boundary's error state so the next render (e.g. the
 *     next successful poll) can mount the tree again without a full reload;
 *   - "Reload" does a hard page reload as a last resort.
 *
 * It must be mounted INSIDE the data providers (QueryClientProvider) so that
 * clearing the error re-renders children against live query state.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";

export interface ErrorBoundaryProps {
  children: ReactNode;
  /** Optional custom fallback renderer; receives a reset callback. */
  fallback?: (reset: () => void) => ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

export default class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surface the error in the browser console for debugging; the dev terminal
    // never sees render-time exceptions.
    console.error("Unhandled render error caught by ErrorBoundary:", error, info);
  }

  private reset = (): void => {
    this.setState({ hasError: false });
  };

  render(): ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }

    if (this.props.fallback) {
      return this.props.fallback(this.reset);
    }

    return (
      <div
        role="alert"
        className="m-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800"
      >
        <p className="font-medium">Something went wrong — retrying…</p>
        <p className="mt-1 text-amber-700">
          The dashboard hit a temporary display error. It will recover on its
          own; you can also try again or reload.
        </p>
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            onClick={this.reset}
            className="rounded-md border border-amber-300 bg-white px-3 py-1 font-medium text-amber-800 hover:bg-amber-100"
          >
            Try again
          </button>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-md border border-amber-300 bg-white px-3 py-1 font-medium text-amber-800 hover:bg-amber-100"
          >
            Reload
          </button>
        </div>
      </div>
    );
  }
}
