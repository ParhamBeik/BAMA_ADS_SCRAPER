import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { Direction } from "radix-ui";
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
      {/* Radix reads direction from this provider and defaults to `ltr` when it
          is missing — `dir="rtl"` on <html> is not something it looks at. Every
          primitive was therefore laying itself out and moving focus as if the
          page ran left to right: the deal-board tabs rendered first-tab-leftmost
          directly under a row of window presets that rendered first-preset-
          rightmost, and ArrowRight walked the tab strip backwards. One provider
          fixes it for every primitive, including ones added later. */}
      <Direction.Provider dir="rtl">
        <QueryClientProvider client={queryClient}>
          <ThemeProvider>
            <AuthProvider>
              <BrowserRouter>
                <App />
              </BrowserRouter>
            </AuthProvider>
          </ThemeProvider>
        </QueryClientProvider>
      </Direction.Provider>
    </ErrorBoundary>
  </StrictMode>,
);
