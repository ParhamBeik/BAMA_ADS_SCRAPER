import { type FormEvent, useState } from "react";
import { api, ApiError } from "../../api/client";
import { useAuth } from "../../auth";

export function Account() {
  const { me, refresh, logout } = useAuth();
  const [fullName, setFullName] = useState(me?.user.full_name ?? "");
  const [message, setMessage] = useState("");

  if (!me) return <p dir="rtl">برای مشاهده حساب وارد شوید.</p>;

  async function save(e: FormEvent) {
    e.preventDefault();
    try {
      await api.patch("/api/auth/me/", { full_name: fullName });
      await refresh();
      setMessage("ذخیره شد.");
    } catch (err) {
      setMessage(err instanceof ApiError ? err.detail : "خطا");
    }
  }

  async function requestPro() {
    try {
      await api.post("/api/auth/pro-request/", { message: "درخواست ارتقا به Pro" });
      setMessage("درخواست Pro ثبت شد.");
    } catch (err) {
      setMessage(err instanceof ApiError ? err.detail : "خطا");
    }
  }

  async function deleteAccount() {
    if (!confirm("حساب غیرفعال شود؟ تا ۳۰ روز قابل بازیابی است.")) return;
    await api.post("/api/auth/delete/");
    await logout();
  }

  return (
    <div className="stack" dir="rtl">
      <h2>حساب کاربری</h2>
      <p className="muted">{me.user.email} · طرح {me.plan} · {me.verified ? "تأیید شده" : "تأیید نشده"}</p>
      <form onSubmit={save} className="form-stack card">
        <label>
          نام
          <input value={fullName} onChange={(e) => setFullName(e.target.value)} />
        </label>
        <button className="btn primary" type="submit">ذخیره</button>
      </form>
      {me.plan !== "pro" && me.verified && (
        <button className="btn" onClick={requestPro}>درخواست Pro</button>
      )}
      <button className="btn danger" onClick={deleteAccount}>حذف حساب</button>
      {message && <p>{message}</p>}
    </div>
  );
}
