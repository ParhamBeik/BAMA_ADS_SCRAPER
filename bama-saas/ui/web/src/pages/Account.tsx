/**
 * Account: change password, and sign out every device.
 *
 * Forgot-password by email does not exist — this host has no mail backend.
 * A locked-out user needs an operator to set a password in Django admin.
 */
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { ApiError, api } from "../api";
import { Card } from "../ui";
import {
  FormError,
  PasswordField,
  Requirements,
  passwordChecks,
} from "./AuthLayout";
import { Button } from "../components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "../components/ui/dialog";

function humanPasswordError(err: unknown): string {
  if (!(err instanceof ApiError) || err.status !== 400) {
    if (err instanceof ApiError && err.status === 429) {
      return "تلاش بیش از حد. یک دقیقه صبر کنید و دوباره تلاش کنید.";
    }
    return "تغییر گذرواژه ناموفق بود.";
  }
  const body = err.body as
    | { current_password?: string[]; new_password?: string[]; detail?: string }
    | undefined;
  const current = body?.current_password?.[0];
  const next = body?.new_password?.[0];
  if (current) return "گذرواژه فعلی نادرست است.";
  if (next?.includes("different")) return "گذرواژه جدید باید با گذرواژه فعلی فرق داشته باشد.";
  if (next) return "گذرواژه جدید پذیرفته نشد. شرایط را بررسی کنید.";
  return "لطفاً اطلاعات واردشده را بررسی کنید.";
}

export function Account() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showCurrent, setShowCurrent] = useState(false);
  const [showNext, setShowNext] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [killing, setKilling] = useState(false);

  const checks = passwordChecks(next);
  const matches = Boolean(next) && next === confirm;
  const ready = Boolean(current) && checks.every((c) => c.ok) && matches && current !== next;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!ready || submitting) return;
    setError(null);
    setSaved(false);
    setSubmitting(true);
    try {
      await api.post("/api/auth/password/", {
        current_password: current,
        new_password: next,
      });
      setCurrent("");
      setNext("");
      setConfirm("");
      setSaved(true);
    } catch (err) {
      setError(humanPasswordError(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function killSessions() {
    setKilling(true);
    try {
      await api.post("/api/auth/logout-everywhere/");
    } finally {
      setKilling(false);
      setConfirmOpen(false);
      await logout();
      navigate("/login", { replace: true });
    }
  }

  return (
    <div className="grid cols-2">
      <Card title="گذرواژه">
        <form className="auth-form" onSubmit={onSubmit} noValidate>
          <FormError message={error} />
          {saved && (
            <p className="up" role="status">گذرواژه به‌روز شد.</p>
          )}
          <PasswordField
            id="account-current"
            label="گذرواژه فعلی"
            value={current}
            onChange={setCurrent}
            show={showCurrent}
            onToggle={() => setShowCurrent((v) => !v)}
            autoComplete="current-password"
            placeholder="گذرواژه فعلی"
          />
          <PasswordField
            id="account-new"
            label="گذرواژه جدید"
            value={next}
            onChange={setNext}
            show={showNext}
            onToggle={() => setShowNext((v) => !v)}
            autoComplete="new-password"
            placeholder="گذرواژه جدید"
            describedBy="account-requirements"
            invalid={Boolean(next) && !checks.every((c) => c.ok)}
          />
          {next && <Requirements checks={checks} id="account-requirements" />}
          <PasswordField
            id="account-confirm"
            label="تکرار گذرواژه جدید"
            value={confirm}
            onChange={setConfirm}
            show={showConfirm}
            onToggle={() => setShowConfirm((v) => !v)}
            autoComplete="new-password"
            placeholder="گذرواژه را دوباره وارد کنید"
            invalid={Boolean(confirm) && !matches}
          />
          {confirm && !matches && (
            <small className="down auth-inline-hint">گذرواژه‌ها یکسان نیستند</small>
          )}
          <Button type="submit" disabled={!ready || submitting}>
            {submitting ? "در حال ذخیره…" : "تغییر گذرواژه"}
          </Button>
        </form>
      </Card>

      <Card title="نشست‌ها">
        <p className="stat-sub" dir="rtl">
          وارد شده با <span dir="ltr" className="font-mono">{user?.email}</span>
        </p>
        <p className="empty-hint">
          خروج از همه دستگاه‌ها نشست‌های دیگر را می‌بندد. این دستگاه هم خارج
          می‌شود و باید دوباره وارد شوید.
        </p>
        <Button variant="outline" onClick={() => setConfirmOpen(true)}>
          خروج از همه دستگاه‌ها
        </Button>
      </Card>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>خروج از همه دستگاه‌ها؟</DialogTitle>
            <DialogDescription>
              هر نشست باز — از جمله همین صفحه — بسته می‌شود.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmOpen(false)}>انصراف</Button>
            <Button variant="destructive" onClick={killSessions} disabled={killing}>
              {killing ? "در حال خروج…" : "خروج از همه"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
