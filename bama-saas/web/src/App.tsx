/**
 * Shell and routing.
 *
 * The interface is English; the *data* is Persian and stays that way, wrapped in
 * `<Fa>` so brand names and titles render right-to-left inside English chrome
 * rather than being transliterated into something that matches no listing on the
 * site.
 */
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Activity, BarChart3, Bookmark, LayoutDashboard, Search } from "lucide-react";
import { Explorer } from "./pages/Explorer";
import { MyMarket } from "./pages/MyMarket";
import { Operations } from "./pages/Operations";
import { Overview } from "./pages/Overview";
import { Research } from "./pages/Research";
import { useTheme, type ThemeChoice } from "./theme";

const NAV = [
  { to: "/", label: "Market Overview", icon: LayoutDashboard, end: true,
    sub: "How the market as a whole is moving" },
  { to: "/explore", label: "Buyer Explorer", icon: Search, end: false,
    sub: "Find a car and check what it is worth" },
  { to: "/research", label: "Research", icon: BarChart3, end: false,
    sub: "Cohort analytics — liquidity, negotiation, retention" },
  { to: "/my-market", label: "My Market", icon: Bookmark, end: false,
    sub: "Saved cars, alerts and notifications" },
  { to: "/ops", label: "Operations", icon: Activity, end: false,
    sub: "Crawl health and scheduled jobs" },
];

export function App() {
  const location = useLocation();
  const current =
    NAV.find((n) =>
      n.end ? location.pathname === n.to : location.pathname.startsWith(n.to),
    ) ?? NAV[0];

  return (
    <div className="app">
      <nav className="sidebar" aria-label="Main">
        <div className="brand">Bama Market Intelligence</div>
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
            <p className="page-sub">{current.sub}</p>
          </div>
          <ThemeToggle />
        </header>

        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/explore" element={<Explorer />} />
          <Route path="/research" element={<Research />} />
          <Route path="/my-market" element={<MyMarket />} />
          <Route path="/ops" element={<Operations />} />
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
    <div className="segmented" role="group" aria-label="Colour theme">
      {options.map((option) => (
        <button
          key={option}
          className={choice === option ? "on" : ""}
          aria-pressed={choice === option}
          onClick={() => setChoice(option)}
        >
          {option[0].toUpperCase() + option.slice(1)}
        </button>
      ))}
    </div>
  );
}
