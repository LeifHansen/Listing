import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@/styles/tokens.css";
import App from "@/App";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { installClientErrorReporting } from "@/lib/clientErrors";

// Before createRoot, so a throw during the very first render is caught by the
// window handler even though the boundary below is not mounted yet. Installed
// here rather than in index.html: that file's inline script is CSP-hashed from
// the built output at startup, and a second one there could not reach the
// bundle's apiUrl anyway.
installClientErrorReporting();

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
