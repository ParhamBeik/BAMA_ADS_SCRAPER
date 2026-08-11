import { type FormEvent, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, ApiError } from "../../api/client";
import { useAuth } from "../../auth";

export function Verify() {
  const [params] = useSearchParams();
  const { refresh, me } = useAuth();
  const [message, setMessage] = useState("در حال تأیید…");
  const [done, setDone] = useState(false);

  useEffect(() => {
    const token = params.get("token");
    if (!token) {
      setMessage(me?.verified ? "ایمیل شما تأیید شده است." : "لینک تأیید را از ایمیل خود باز کنید، یا درخواست ارسال مجدد بدهید.");
      return;
    }
    void api.post("/api/auth/verify/", { token })
      .then(async () => {
        setDone(true);
        setMessage("ایمیل با موفقیت تأیید شد.");
        await refresh();
      })
      .catch((err) => setMessage(err instanceof ApiError ? err.detail : "تأیید ناموفق بود"));
  }, [params, refresh, me?.verified]);

  async function resend(e: FormEvent) {
    e.preventDefault();
    try {
      await api.post("/api/auth/resend-verification/");
      setMessage("ایمیل تأیید دوباره ارسال شد.");
    } catch (err) {
      setMessage(err instanceof ApiError ? err.detail : "ارسال ناموفق بود");
    }
  }

  return (
    <div className="auth-card" dir="rtl">
      <h1>تأیید ایمیل</h1>
      <p>{message}</p>
      {!done && !me?.verified && (
        <button className="btn" onClick={resend}>ارسال مجدد</button>
      )}
    </div>
  );
}
