import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "../../auth";
import { ControlHealth } from "./Health";
import { ControlUsers } from "./Users";
import { ControlReview } from "./Review";
import { ControlJobs } from "./Jobs";

export function ControlApp() {
  const { me, loading } = useAuth();
  if (loading) return <p>…</p>;
  if (!me?.user.is_staff) return <Navigate to="/" replace />;

  return (
    <div className="app control-app" dir="rtl">
      <nav className="sidebar">
        <div className="brand">مرکز کنترل</div>
        <NavLink to="/control" end className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>سلامت</NavLink>
        <NavLink to="/control/users" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>کاربران</NavLink>
        <NavLink to="/control/review" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>بازبینی</NavLink>
        <NavLink to="/control/jobs" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>کارها</NavLink>
        <NavLink to="/" className="nav-item">بازگشت به سایت</NavLink>
      </nav>
      <main className="main">
        <Routes>
          <Route index element={<ControlHealth />} />
          <Route path="users" element={<ControlUsers />} />
          <Route path="review" element={<ControlReview />} />
          <Route path="jobs" element={<ControlJobs />} />
        </Routes>
      </main>
    </div>
  );
}
