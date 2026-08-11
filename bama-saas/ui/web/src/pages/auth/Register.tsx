import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../../auth";
import { ApiError } from "../../api/client";

export function Register() {
  const { register } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await register(email, password, fullName);
      nav("/verify");
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "ثبت‌نام ناموفق بود");
    }
  }

  return (
    <div className="auth-card" dir="rtl">
      <h1>ثبت‌نام</h1>
      <form onSubmit={onSubmit} className="form-stack">
        <label>
          نام
          <input value={fullName} onChange={(e) => setFullName(e.target.value)} />
        </label>
        <label>
          ایمیل
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          رمز عبور
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
        </label>
        {error && <p className="form-error">{error}</p>}
        <button type="submit" className="btn primary">ایجاد حساب</button>
      </form>
      <p className="muted">حساب دارید؟ <Link to="/login">ورود</Link></p>
    </div>
  );
}
