import { type FormEvent, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../../api/client";

export function ResetPassword() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    try {
      await api.post("/api/auth/password-reset/confirm/", {
        uid: params.get("uid"),
        token: params.get("token"),
        password,
      });
      nav("/login");
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "خطا");
    }
  }

  return (
    <div className="auth-card" dir="rtl">
      <h1>رمز جدید</h1>
      <form onSubmit={onSubmit} className="form-stack">
        <label>
          رمز عبور جدید
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
        </label>
        {error && <p className="form-error">{error}</p>}
        <button className="btn primary" type="submit">ذخیره</button>
      </form>
    </div>
  );
}
