import { Component, type ErrorInfo, type ReactNode } from "react";
import { ErrorState } from "./States";

interface State { error: Error | null }

/** Keeps a rendering error inside one page instead of blanking the whole app. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Page failed to render", error, info.componentStack);
  }

  componentDidUpdate(prev: { resetKey?: string }): void {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    if (this.state.error) {
      return <ErrorState error={new Error(`This page could not be displayed: ${this.state.error.message}`)} onRetry={() => this.setState({ error: null })} />;
    }
    return this.props.children;
  }
}
