/**
 * Seven screens, one shell (Persian RTL).
 *
 * The app is local and single-user, so there is no session to check and no route
 * to gate — `/control` is an ordinary page, and record inspection lives in Django
 * admin rather than in a bespoke staff UI.
 */
import { lazy, Suspense, type ReactNode } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import {
  Activity, BarChart3, Bookmark, LayoutDashboard, Percent, Search,
} from "lucide-react";
import { useTheme, type ThemeChoice } from "./theme";
import { Deals } from "./pages/Deals";
import { Explorer } from "./pages/Explorer";
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
  { to: "/", label: "پیشنهادها", icon: Percent, end: true },
  { to: "/explore", label: "کاوش", icon: Search, end: false },
  { to: "/market", label: "نمای بازار", icon: LayoutDashboard, end: false },
  { to: "/research", label: "تحقیق", icon: BarChart3, end: false },
  { to: "/saved", label: "ذخیره‌شده", icon: Bookmark, end: false },
  { to: "/control", label: "کنترل", icon: Activity, end: false },
];

export function App() {
  const location = useLocation();
  const current = NAV.find((n) =>
    n.end ? location.pathname === n.to : location.pathname.startsWith(n.to),
  ) ?? NAV[0];

  return (
    <div className="app" dir="rtl" lang="fa">
      <nav className="sidebar" aria-label="اصلی">
        <div className="brand">بازار باما</div>
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
