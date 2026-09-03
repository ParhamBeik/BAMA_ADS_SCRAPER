import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { AuthProvider } from "./auth";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ThemeProvider } from "./theme";
// Fonts are declared in styles.css, not imported here: an @import in this file
// means the browser cannot discover them until the JS module graph has loaded
// and run. Still bundled rather than fetched from fonts.googleapis.com, which
// is unreliable from Iran and left the whole interface on Tahoma when it failed.
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      staleTime: 60_000,
      retry: (count, error: unknown) => {
        const status = (error as { status?: number })?.status;
        if (status && status < 500) return false;
        return count < 2;
      },
    },
  },
});

// The boundary is outside every provider on purpose. A provider that throws
// during its own render takes the tree down just as thoroughly as a page does,
// and a boundary nested inside one cannot catch its parent.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>
          <AuthProvider>
            <BrowserRouter>
              <App />
            </BrowserRouter>
          </AuthProvider>
        </ThemeProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
);
