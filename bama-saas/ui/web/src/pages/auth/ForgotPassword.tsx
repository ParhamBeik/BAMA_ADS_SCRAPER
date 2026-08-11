import { type FormEvent, useState } from "react";
import { api, ApiError } from "../../api/client";

export function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    try {
      await api.post("/api/auth/password-reset/", { email });
      setMessage("اگر این ایمیل ثبت شده باشد، لینک بازیابی ارسال شد.");
    } catch (err) {
      setMessage(err instanceof ApiError ? err.detail : "خطا");
    }
  }

  return (
    <div className="auth-card" dir="rtl">
      <h1>بازیابی رمز</h1>
      <form onSubmit={onSubmit} className="form-stack">
        <label>
          ایمیل
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <button className="btn primary" type="submit">ارسال لینک</button>
      </form>
      {message && <p className="muted">{message}</p>}
    </div>
  );
}
