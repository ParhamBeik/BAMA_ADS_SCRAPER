import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../auth";
import { ApiError } from "../../api/client";

export function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const me = await login(email, password);
      nav(me.user.is_staff ? "/control" : "/explore");
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "ورود ناموفق بود");
    }
  }

  return (
    <div className="auth-card" dir="rtl">
      <h1>ورود</h1>
      <form onSubmit={onSubmit} className="form-stack">
        <label>
          ایمیل
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          رمز عبور
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <p className="form-error">{error}</p>}
        <button type="submit" className="btn primary">ورود</button>
      </form>
    </div>
  );
}
