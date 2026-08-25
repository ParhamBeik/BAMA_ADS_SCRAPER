import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { AuthProvider } from "./auth";
import { ThemeProvider } from "./theme";
// Bundled, not fetched from fonts.googleapis.com. That host is unreliable from
// Iran, which is where this app's users are, so the entire interface was
// silently falling back to Tahoma — the one thing a Persian-first UI cannot
// afford. Vazirmatn carries the Persian glyphs and a Latin set good enough that
// Inter is no longer needed; JetBrains Mono stays for the audited numbers.
import "@fontsource-variable/vazirmatn";
import "@fontsource-variable/jetbrains-mono";
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

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <AuthProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </AuthProvider>
      </ThemeProvider>
    </QueryClientProvider>
  </StrictMode>,
);
