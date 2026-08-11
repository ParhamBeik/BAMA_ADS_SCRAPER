/**
 * Consumer shell (Persian RTL) + lazy staff control center.
 */
import { lazy, Suspense } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import {
  BarChart3, Bookmark, GitCompare, LayoutDashboard, Search, UserRound,
} from "lucide-react";
import { useAuth } from "./auth";
import { useTheme, type ThemeChoice } from "./theme";
import { Landing } from "./pages/Landing";
import { Explorer } from "./pages/Explorer";
import { MyMarket } from "./pages/MyMarket";
import { Overview } from "./pages/Overview";
import { ListingDetail } from "./pages/ListingDetail";
import { BrandPage } from "./pages/BrandPage";
import { ModelPage } from "./pages/ModelPage";
import { Login } from "./pages/auth/Login";
import { Register } from "./pages/auth/Register";
import { Verify } from "./pages/auth/Verify";
import { ForgotPassword } from "./pages/auth/ForgotPassword";
import { ResetPassword } from "./pages/auth/ResetPassword";
import { Account } from "./pages/auth/Account";

const ControlApp = lazy(() =>
  import("./pages/control/ControlApp").then((m) => ({ default: m.ControlApp })),
);
const Research = lazy(() =>
  import("./pages/Research").then((m) => ({ default: m.Research })),
);
const CompareLazy = lazy(() =>
  import("./pages/Compare").then((m) => ({ default: m.Compare })),
);

const NAV = [
  { to: "/", label: "خانه", icon: LayoutDashboard, end: true },
  { to: "/explore", label: "کاوش", icon: Search, end: false },
  { to: "/research", label: "تحقیق", icon: BarChart3, end: false },
  { to: "/compare", label: "مقایسه", icon: GitCompare, end: false },
  { to: "/my-market", label: "بازار من", icon: Bookmark, end: false },
  { to: "/market", label: "نمای بازار", icon: LayoutDashboard, end: false },
];

export function App() {
  const location = useLocation();
  if (location.pathname.startsWith("/control")) {
    return (
      <Suspense fallback={<p dir="rtl">در حال بارگذاری…</p>}>
        <ControlApp />
      </Suspense>
    );
  }

  const { me, logout } = useAuth();
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
        <div className="sidebar-foot">
          {me ? (
            <>
              <Link className="nav-item" to="/account"><UserRound size={16} /> حساب</Link>
              <button className="nav-item linkish" onClick={() => void logout()}>خروج</button>
            </>
          ) : (
            <Link className="nav-item" to="/login">ورود</Link>
          )}
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
          <Route path="/" element={<Landing />} />
          <Route path="/explore" element={<Explorer />} />
          <Route path="/research" element={<Suspense fallback={<p>…</p>}><Research /></Suspense>} />
          <Route path="/compare" element={<Suspense fallback={<p>…</p>}><CompareLazy /></Suspense>} />
          <Route path="/my-market" element={<MyMarket />} />
          <Route path="/market" element={<Overview />} />
          <Route path="/listing/:code" element={<ListingDetail />} />
          <Route path="/brand/:slug" element={<BrandPage />} />
          <Route path="/model/:id" element={<ModelPage />} />
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route path="/verify" element={<Verify />} />
          <Route path="/forgot-password" element={<ForgotPassword />} />
          <Route path="/reset-password" element={<ResetPassword />} />
          <Route path="/account" element={<Account />} />
          <Route path="/ops" element={<Navigate to="/control" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
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
