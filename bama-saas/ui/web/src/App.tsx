/**
 * Seven screens, one shell (Persian RTL for data, English chrome).
 *
 * Deployed instances require a session (see config/settings/prod.py); `useAuth`
 * decides between the login screen and the app shell, and the shell itself never
 * has to think about it again below this point.
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
import { Overview } from "./pages/Overview";
import { Saved } from "./pages/Saved";
import { ListingDetail } from "./pages/ListingDetail";

const Control = lazy(() =>
  import("./pages/Control").then((m) => ({ default: m.Control })),
);
const Research = lazy(() =>
  import("./pages/Research").then((m) => ({ default: m.Research })),
);

const NAV = [
  { to: "/", label: "Deals", icon: Percent, end: true },
  { to: "/explore", label: "Explore", icon: Search, end: false },
  { to: "/market", label: "Market", icon: LayoutDashboard, end: false },
  { to: "/research", label: "Research", icon: BarChart3, end: false },
  { to: "/saved", label: "Saved", icon: Bookmark, end: false },
  { to: "/control", label: "Control", icon: Activity, end: false },
];

export function App() {
  const { user, loading } = useAuth();

  if (loading) return null;
  if (!user) return <Login />;
  return <AppShell />;
}

function AppShell() {
  const location = useLocation();
  const { logout } = useAuth();
  const current = NAV.find((n) =>
    n.end ? location.pathname === n.to : location.pathname.startsWith(n.to),
  ) ?? NAV[0];

  return (
    <div className="app" dir="rtl">
      <nav className="sidebar" aria-label="Main">
        <div className="brand">Bama Market</div>
        {NAV.map(({ to, label, icon: Icon, end }) => (
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
            Log out
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
          <Route path="/control" element={<Lazy><Control /></Lazy>} />
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
  const options: ThemeChoice[] = ["light", "system", "dark"];
  return (
    <div className="segmented" role="group" aria-label="پوسته">
      {options.map((option) => (
        <button
          key={option}
          className={choice === option ? "on" : ""}
          aria-pressed={choice === option}
          onClick={() => setChoice(option)}
        >
          {option}
        </button>
      ))}
    </div>
  );
}
