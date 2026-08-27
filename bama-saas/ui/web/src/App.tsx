/**
 * Six screens, one shell. Persian throughout, RTL from <html> — the direction
 * used to be set per page, so a screen that forgot it laid itself out backwards.
 *
 * Deployed instances require a session (see config/settings.py); `useAuth`
 * decides between the signed-out routes and the app shell, and the shell itself
 * never has to think about it again below this point.
 *
 * The chrome is one floating header (see components/AppHeader) rather than a
 * sidebar plus a title bar. The page title went with them: every screen already
 * says what it is, and the nav already says where you are.
 */
import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { useAuth } from "./auth";
import { AppHeader, AuthHeader, MobileNav } from "./components/AppHeader";
import { Deals } from "./pages/Deals";
import { Explorer } from "./pages/Explorer";
import { Home } from "./pages/Home";
import { Login } from "./pages/Login";
import { Signup } from "./pages/Signup";
import { Saved } from "./pages/Saved";
import { ListingDetail } from "./pages/ListingDetail";

const Control = lazy(() =>
  import("./pages/Control").then((m) => ({ default: m.Control })),
);
const Analyse = lazy(() =>
  import("./pages/Analyse").then((m) => ({ default: m.Analyse })),
);

export function App() {
  const { user, loading } = useAuth();

  if (loading) return null;
  return user ? <AppShell /> : <AuthRoutes />;
}

/**
 * Signed-out routes. Two real URLs rather than one screen with a toggle, so the
 * back button works and a signup link is shareable. Anything else redirects to
 * sign-in and does not 404 — a bookmark saved while logged in should land
 * somewhere sensible.
 */
function AuthRoutes() {
  return (
    // `relative`, because the signed-out controls are pinned to its top corner
    // rather than sitting in a header band above the form.
    <div className="relative">
      <Routes>
        <Route
          path="/login"
          element={<><AuthHeader to="/signup" label="ساخت حساب" /><Login /></>}
        />
        <Route
          path="/signup"
          element={<><AuthHeader to="/login" label="ورود" /><Signup /></>}
        />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </div>
  );
}

function AppShell() {
  const { user } = useAuth();

  return (
    <div className="min-h-dvh">
      <AppHeader />
      <main className="mx-auto max-w-[1600px] px-4 pt-2 pb-24 sm:px-6 lg:pb-16">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/deals" element={<Deals />} />
          <Route path="/explore" element={<Explorer />} />
          <Route path="/analyse" element={<Lazy><Analyse /></Lazy>} />
          <Route path="/saved" element={<Saved />} />
          <Route path="/listing/:code" element={<ListingDetail />} />
          <Route
            path="/control"
            element={
              user?.is_staff ? (
                <Lazy><Control /></Lazy>
              ) : (
                <Navigate to="/" replace />
              )
            }
          />
          {/* The two screens that used to live at these paths. A bookmark or a
              shared link from before the merge should land on the page that
              answers the same question, not on a redirect to the front page. */}
          <Route path="/market" element={<Navigate to="/analyse" replace />} />
          <Route path="/research" element={<Navigate to="/analyse" replace />} />
          <Route path="/research/:modelId" element={<LegacyResearchRedirect />} />
          {/* Signed in, so the auth routes are meaningless here. */}
          <Route path="/login" element={<Navigate to="/" replace />} />
          <Route path="/signup" element={<Navigate to="/" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <MobileNav />
    </div>
  );
}

/** `/research/42` was the old per-model analysis URL; it is now a scope query. */
function LegacyResearchRedirect() {
  const { modelId } = useParams();
  return <Navigate to={modelId ? `/analyse?model=${modelId}` : "/analyse"} replace />;
}

function Lazy({ children }: { children: ReactNode }) {
  return <Suspense fallback={<p dir="rtl">در حال بارگذاری…</p>}>{children}</Suspense>;
}
