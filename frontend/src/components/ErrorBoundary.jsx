import { Component } from "react";
import { reportClientError } from "@/lib/clientErrors";

// The only React API for catching a render crash is a class component, so
// this is the one class in the codebase. No new dependency: react-error-
// boundary would be a package to carry for forty lines.
//
// Before this, a throw during render unmounted the whole tree and left a white
// screen. The seller saw nothing, could do nothing, and the server never
// learned it had happened.
//
// It still calls console.error. That matters more than it looks: scripts/
// smoke.mjs fails the CI smoke gate on a page error, and it is the only gate
// that catches "a screen a seller cannot open". A boundary that swallowed the
// crash would make the app look healthier to CI while being just as broken.
export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error, info) {
    // Keep the crash visible to smoke.mjs and to anyone with devtools open.
    console.error(error);
    reportClientError("react", error, {
      componentStack: info && info.componentStack,
    });
  }

  render() {
    if (!this.state.failed) return this.props.children;
    if (this.props.fallback) return this.props.fallback;
    // Deliberately plain markup and inline styles: this renders precisely when
    // something in the tree is broken, so it must not depend on the design
    // system, the store, the toaster, or anything else that might be the
    // thing that just failed.
    return (
      <div role="alert" style={{
        padding: "2rem", margin: "2rem auto", maxWidth: "32rem",
        fontFamily: "system-ui, sans-serif", textAlign: "center",
      }}>
        <h1 style={{ fontSize: "1.125rem", marginBottom: "0.5rem" }}>
          This screen ran into a problem
        </h1>
        <p style={{ fontSize: "0.875rem", lineHeight: 1.5, opacity: 0.75 }}>
          Nothing you were working on has been lost — it is saved on the
          server. Reloading usually clears this.
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          style={{
            marginTop: "1.25rem", padding: "0.5rem 1.25rem",
            fontSize: "0.875rem", cursor: "pointer", borderRadius: "0.5rem",
            border: "1px solid currentColor", background: "transparent",
          }}
        >
          Reload
        </button>
      </div>
    );
  }
}
