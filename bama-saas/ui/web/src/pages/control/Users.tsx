import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Async } from "../../ui";

type Row = {
  id: string; email: string; plan: string | null; is_active: boolean;
  email_verified_at: string | null; is_staff: boolean;
};

export function ControlUsers() {
  const [q, setQ] = useState("");
  const [ordering, setOrdering] = useState("-date_joined");
  const qc = useQueryClient();
  const users = useQuery({
    queryKey: ["admin-users", q, ordering],
    queryFn: ({ signal }) => api.get<{ results: Row[] }>(`/api/admin/users/?q=${encodeURIComponent(q)}&ordering=${ordering}`, signal),
  });
  const pro = useQuery({
    queryKey: ["admin-pro"],
    queryFn: ({ signal }) => api.get<{ id: string; user_email: string; message: string }[]>("/api/admin/pro-requests/?status=pending", signal),
  });

  async function setPlan(id: string, plan_type: string) {
    await api.patch(`/api/admin/users/${id}/`, { plan_type, days: 30 });
    await qc.invalidateQueries({ queryKey: ["admin-users"] });
  }

  async function approve(id: string) {
    await api.post(`/api/admin/pro-requests/${id}/`, { action: "approve", days: 30 });
    await qc.invalidateQueries({ queryKey: ["admin-pro"] });
  }

  return (
    <div className="stack">
      <h1>کاربران</h1>
      <div className="row">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="جستجوی ایمیل" />
        <button className="btn" onClick={() => setOrdering(ordering === "email" ? "-email" : "email")}>مرتب‌سازی ایمیل</button>
      </div>
      <Async query={users}>
        {(data) => (
          <table className="table">
            <thead><tr><th>ایمیل</th><th>طرح</th><th>وضعیت</th><th>عملیات</th></tr></thead>
            <tbody>
              {data.results.map((u) => (
                <tr key={u.id}>
                  <td>{u.email}{u.is_staff ? " ★" : ""}</td>
                  <td>{u.plan ?? "—"}</td>
                  <td>{u.is_active ? (u.email_verified_at ? "تأیید شده" : "تأیید نشده") : "معلق"}</td>
                  <td>
                    <button className="btn" onClick={() => setPlan(u.id, "pro")}>Pro</button>
                    <button className="btn" onClick={() => setPlan(u.id, "free")}>Free</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Async>
      <h2>درخواست‌های Pro</h2>
      <Async query={pro}>
        {(rows) => rows.length === 0 ? <p className="muted">موردی نیست</p> : (
          <ul>{rows.map((r) => (
            <li key={r.id}>{r.user_email}: {r.message} <button className="btn" onClick={() => approve(r.id)}>تأیید</button></li>
          ))}</ul>
        )}
      </Async>
    </div>
  );
}
