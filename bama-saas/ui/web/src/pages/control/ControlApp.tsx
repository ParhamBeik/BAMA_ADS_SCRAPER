import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "../../auth";
import { ControlHealth } from "./Health";
import { ControlUsers } from "./Users";
import { ControlReview } from "./Review";
import { ControlJobs } from "./Jobs";
import { ControlAds } from "./Ads";
import { ControlCatalog } from "./Catalog";
import { ControlIngestion } from "./Ingestion";

export function ControlApp() {
  const { me, loading } = useAuth();
  if (loading) return <p>…</p>;
  if (!me?.user.is_staff) return <Navigate to="/login" replace />;

  return (
    <div className="app control-app" dir="rtl">
      <nav className="sidebar">
        <div className="brand">مرکز کنترل</div>
        <NavLink to="/control" end className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>Status</NavLink>
        <NavLink to="/control/ads" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>Ads</NavLink>
        <NavLink to="/control/catalog" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>Catalog</NavLink>
        <NavLink to="/control/ingestion" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>Ingestion</NavLink>
        <NavLink to="/control/jobs" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>Jobs</NavLink>
        <NavLink to="/control/review" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>Review</NavLink>
        <NavLink to="/control/users" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>Users</NavLink>
        <NavLink to="/" className="nav-item">بازگشت به سایت</NavLink>
      </nav>
      <main className="main" dir="ltr">
        <Routes>
          <Route path="/control">
            <Route index element={<ControlHealth />} />
            <Route path="ads" element={<ControlAds />} />
            <Route path="ads/:code" element={<ControlAds />} />
            <Route path="catalog" element={<ControlCatalog />} />
            <Route path="ingestion" element={<ControlIngestion />} />
            <Route path="jobs" element={<ControlJobs />} />
            <Route path="review" element={<ControlReview />} />
            <Route path="users" element={<ControlUsers />} />
          </Route>
        </Routes>
      </main>
    </div>
  );
}
