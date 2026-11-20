import { Component, StrictMode, type ErrorInfo, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

/** Shows a readable message instead of a blank page if a component crashes at runtime. */
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("NyayaAI UI error:", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div style={{ maxWidth: 640, margin: "60px auto", padding: 24, fontFamily: "system-ui, sans-serif" }}>
        <h2>Something went wrong in the page.</h2>
        <p style={{ color: "#b42318" }}>{this.state.error.message}</p>
        <button type="button" onClick={() => window.location.reload()}>Reload</button>
      </div>
    );
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
