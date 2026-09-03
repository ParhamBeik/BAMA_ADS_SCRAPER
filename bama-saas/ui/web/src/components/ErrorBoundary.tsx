/**
 * The one thing standing between a render error and a black screen.
 *
 * React unmounts the entire tree when a render throws and nothing catches it.
 * This app is client-rendered into `#root`, so "unmount the entire tree" means
 * an empty document body — and `--bg` is `#14120f` in dark mode. A reader sees
 * black, the console message is the only evidence, and nothing on screen says
 * to look there. That is the exact shape of the bug this was written for.
 *
 * Deliberately imports nothing but React. A boundary that renders the design
 * system cannot render when the design system is what threw, and a fallback
 * that throws is caught by no one — React remounts it, it throws again, and the
 * root is torn down for good.
 *
 * The error text is on screen, not just in the console, because the people who
 * hit this are on a phone with no devtools and the only report that helps is one
 * that quotes the message.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Kept: `getDerivedStateFromError` gets the error but not the component
    // stack, and the stack is the half that says *which* panel threw.
    console.error("Unhandled render error:", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    // The failure this was written for, and the only one where "reload" is
    // genuine advice rather than a shrug: a route's chunk did not arrive, so
    // fetching it again is the actual fix. Matched on the message because
    // browsers disagree on the wording but all of them say "dynamically
    // imported module"; a miss here costs the generic copy, not correctness.
    const chunkFailed = /dynamically imported module|Importing a module script failed/i
      .test(error.message);

    return (
      <div
        dir="rtl"
        style={{
          minHeight: "100dvh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "1.5rem",
          // Inline, not a class: if the stylesheet is what failed to load, the
          // class would resolve to nothing and this page would be unreadable
          // for the same reason the app was.
          fontFamily: '"Vazirmatn Variable", Tahoma, system-ui, sans-serif',
          color: "#e7e5e4",
          background: "#14120f",
        }}
      >
        <div style={{ maxWidth: "34rem", textAlign: "center" }}>
          <h1 style={{ fontSize: "1.25rem", fontWeight: 700, marginBottom: "0.75rem" }}>
            {chunkFailed ? "بخشی از برنامه دانلود نشد" : "این صفحه بارگذاری نشد"}
          </h1>
          <p style={{ color: "#a8a29e", lineHeight: 1.9, marginBottom: "1.25rem" }}>
            {chunkFailed
              ? "اتصال اینترنت هنگام دریافت این صفحه قطع شد. بارگذاری دوباره معمولاً مشکل را حل می‌کند."
              : "خطایی رخ داد که انتظارش را نداشتیم. صفحه را دوباره بارگذاری کنید؛ اگر باز هم تکرار شد، متن زیر را برای ما بفرستید."}
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            style={{
              padding: "0.5rem 1.25rem",
              borderRadius: 8,
              border: "1px solid #44403c",
              background: "#1d1a16",
              color: "#e7e5e4",
              cursor: "pointer",
              font: "inherit",
            }}
          >
            بارگذاری دوباره
          </button>
          <pre
            dir="ltr"
            style={{
              marginTop: "1.5rem",
              padding: "0.75rem",
              borderRadius: 8,
              background: "#1d1a16",
              color: "#a8a29e",
              fontSize: "0.75rem",
              textAlign: "left",
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
              maxHeight: "12rem",
              overflow: "auto",
            }}
          >
            {error.message || String(error)}
          </pre>
        </div>
      </div>
    );
  }
}
