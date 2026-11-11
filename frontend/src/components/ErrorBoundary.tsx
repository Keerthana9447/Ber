import { Component, type ErrorInfo, type ReactNode } from "react";
import { ErrorState } from "./ui";

export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("UI error", error, info.componentStack);
  }
  render() {
    if (this.state.error) {
      return <ErrorState message="Something went wrong displaying this page." onRetry={() => this.setState({ error: null })} />;
    }
    return this.props.children;
  }
}
