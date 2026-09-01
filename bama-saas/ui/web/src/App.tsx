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
import { lazy, Suspense, useEffect, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
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
const Budget = lazy(() =>
  import("./pages/Budget").then((m) => ({ default: m.Budget })),
);
const Alerts = lazy(() =>
  import("./pages/Alerts").then((m) => ({ default: m.Alerts })),
);

export function App() {
  const { user, loading } = useAuth();

  if (loading) return null;
  return user ? <AppShell /> : <AuthRoutes />;
}

/**
 * Where the reader was heading when they were asked to sign in.
 *
 * Module-scoped rather than router state: signing in swaps the whole route tree
 * — `AuthRoutes` unmounts and `AppShell` mounts at whatever URL the login form
 * was on — so nothing held in React survives the transition. It does not need
 * `sessionStorage` either, because `login` sets the user in place without a
 * page load, and this module is the same instance on both sides of the swap.
 *
 * Without it every shared link was discarded at the door: a link to one
 * listing, to a filtered board and to an analysis scope all three landed on the
 * front page with no way to tell they had been redirected.
 */
let intended: string | null = null;

function rememberIntent(pathname: string, search: string) {
  // "/" is where an unauthenticated visitor lands anyway, and /login and
  // /signup would bounce the reader straight back out of the app.
  if (pathname === "/" || pathname.startsWith("/login") || pathname.startsWith("/signup")) return;
  intended = pathname + search;
}

/**
 * Redirect to the remembered link, or to the front page.
 *
 * A route element and not an effect: `AppShell` mounts while the URL is still
 * `/login`, whose own route already renders a `<Navigate to="/">`. Two effects
 * both firing on that mount race — child effects run before parent ones, so the
 * front-page redirect won about half the time. Resolving it *inside* the route
 * that was going to redirect anyway removes the race instead of ordering it.
 *
 * `intended` is read during render and cleared in an effect, so React's
 * double-invoked render in development sees the same value twice rather than
 * consuming it on the first pass.
 */
function ResumeOrHome() {
  const target = intended;
  useEffect(() => { intended = null; }, []);
  return <Navigate to={target ?? "/"} replace />;
}

/**
 * Signed-out routes. Two real URLs rather than one screen with a toggle, so the
 * back button works and a signup link is shareable. Anything else redirects to
 * sign-in and does not 404 — a bookmark saved while logged in should land
 * somewhere sensible, and now it lands on the page it named.
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
        <Route path="*" element={<RememberThenLogin />} />
      </Routes>
    </div>
  );
}

function RememberThenLogin() {
  const location = useLocation();
  rememberIntent(location.pathname, location.search);
  return <Navigate to="/login" replace />;
}

function AppShell() {
  const { user } = useAuth();

  return (
    <div className="min-h-dvh">
      {/* Keyboard users tabbed the whole floating header on every page before
          reaching any content. */}
      <a href="#main" className="skip-link">پرش به محتوای اصلی</a>
      <AppHeader />
      <main id="main" tabIndex={-1}
            className="mx-auto max-w-[1600px] px-4 pt-2 pb-24 sm:px-6 lg:pb-16">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/deals" element={<Deals />} />
          <Route path="/explore" element={<Explorer />} />
          <Route path="/analyse" element={<Lazy><Analyse /></Lazy>} />
          <Route path="/budget" element={<Lazy><Budget /></Lazy>} />
          <Route path="/alerts" element={<Lazy><Alerts /></Lazy>} />
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
          {/* Signed in, so the auth routes are meaningless here — but landing
              on one is exactly what happens after signing in, and it is where
              the link the reader actually asked for gets resumed. */}
          <Route path="/login" element={<ResumeOrHome />} />
          <Route path="/signup" element={<ResumeOrHome />} />
          <Route path="*" element={<ResumeOrHome />} />
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
