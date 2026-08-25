/**
 * Seven screens, one shell. Persian throughout, RTL from <html> — the direction
 * used to be set per page, so a screen that forgot it laid itself out backwards.
 *
 * Deployed instances require a session (see config/settings.py); `useAuth`
 * decides between the signed-out routes and the app shell, and the shell itself
 * never has to think about it again below this point.
 */
import { lazy, Suspense, type ReactNode } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import {
  Activity, BarChart3, Bookmark, LayoutDashboard, LogOut, Percent, Search,
} from "lucide-react";
import { useAuth } from "./auth";
import { useTheme, type ThemeChoice } from "./theme";
import { Deals } from "./pages/Deals";
import { Explorer } from "./pages/Explorer";
import { Login } from "./pages/Login";
import { Signup } from "./pages/Signup";
import { Overview } from "./pages/Overview";
import { Saved } from "./pages/Saved";
import { ListingDetail } from "./pages/ListingDetail";
import { BrandMark } from "./ui";

const Control = lazy(() =>
  import("./pages/Control").then((m) => ({ default: m.Control })),
);
const Research = lazy(() =>
  import("./pages/Research").then((m) => ({ default: m.Research })),
);

const NAV = [
  { to: "/", label: "معامله‌ها", icon: Percent, end: true },
  { to: "/explore", label: "جست‌وجو", icon: Search, end: false },
  { to: "/market", label: "بازار", icon: LayoutDashboard, end: false },
  { to: "/research", label: "تحلیل", icon: BarChart3, end: false },
  { to: "/saved", label: "ذخیره‌شده‌ها", icon: Bookmark, end: false },
  { to: "/control", label: "کنترل", icon: Activity, end: false, staffOnly: true },
];

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
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}

function AppShell() {
  const location = useLocation();
  const { user, logout } = useAuth();
  const nav = NAV.filter((item) => !item.staffOnly || user?.is_staff);
  const current = nav.find((n) =>
    n.end ? location.pathname === n.to : location.pathname.startsWith(n.to),
  ) ?? nav[0];

  return (
    <div className="app">
      <nav className="sidebar" aria-label="Main">
        <div className="brand brand-lockup">
          <BrandMark size={24} />
          <span>بازار خودرو باما</span>
        </div>
        {nav.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
          >
            <Icon size={16} />
            {label}
          </NavLink>
        ))}
        <div className="sidebar-foot">
          <button className="nav-item linkish" onClick={() => logout()}>
            <LogOut size={16} />
            خروج
          </button>
        </div>
      </nav>

      <main className="main">
        <header className="topbar">
          <div>
            <h1 className="page-title">{current.label}</h1>
          </div>
          <ThemeToggle />
        </header>

        <Routes>
          <Route path="/" element={<Deals />} />
          <Route path="/explore" element={<Explorer />} />
          <Route path="/market" element={<Overview />} />
          <Route path="/research" element={<Lazy><Research /></Lazy>} />
          <Route path="/research/:modelId" element={<Lazy><Research /></Lazy>} />
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
          {/* Signed in, so the auth routes are meaningless here. */}
          <Route path="/login" element={<Navigate to="/" replace />} />
          <Route path="/signup" element={<Navigate to="/" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function Lazy({ children }: { children: ReactNode }) {
  return <Suspense fallback={<p dir="rtl">در حال بارگذاری…</p>}>{children}</Suspense>;
}

function ThemeToggle() {
  const { choice, setChoice } = useTheme();
  const options: [ThemeChoice, string][] = [
    ["light", "روشن"],
    ["system", "سیستم"],
    ["dark", "تیره"],
  ];
  return (
    <div className="segmented" role="group" aria-label="پوسته">
      {options.map(([option, label]) => (
        <button
          key={option}
          className={choice === option ? "on" : ""}
          aria-pressed={choice === option}
          onClick={() => setChoice(option)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
