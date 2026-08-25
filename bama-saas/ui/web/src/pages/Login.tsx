import { useState, type FormEvent } from "react";
import { ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import { AuthLayout, EMAIL_RE, EmailField, FormError, PasswordField } from "./AuthLayout";

export function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [emailTouched, setEmailTouched] = useState(false);
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const emailValid = EMAIL_RE.test(email.trim());
  // Deliberately not checking the password against the signup rules here: an
  // account created before the rules changed must still be able to log in.
  const ready = emailValid && Boolean(password);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!ready || submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await login(email.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "خطایی رخ داد. دوباره تلاش کنید.");
      setPassword("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout
      eyebrow="خوش آمدید"
      heading="به حساب خود وارد شوید"
      intro="از همان‌جا که رها کردید ادامه دهید: معامله‌ها، تحلیل‌ها و آگهی‌های ذخیره‌شده."
      footer={
        <>
          تازه به بازار خودرو باما آمده‌اید؟ <Link to="/signup">حساب بسازید</Link>
        </>
      }
    >
      <form className="auth-form" onSubmit={onSubmit} noValidate>
        <FormError message={error} />

        <EmailField
          value={email}
          onChange={setEmail}
          onBlur={() => setEmailTouched(true)}
          touched={emailTouched}
          hint={
            email && !emailValid
              ? { text: "یک نشانی ایمیل معتبر وارد کنید", tone: "down" }
              : null
          }
        />

        <PasswordField
          id="auth-password"
          label="گذرواژه"
          value={password}
          onChange={setPassword}
          show={show}
          onToggle={() => setShow((v) => !v)}
          autoComplete="current-password"
          placeholder="گذرواژه خود را وارد کنید"
        />

        <button className="primary auth-submit" type="submit" disabled={!ready || submitting}>
          {submitting ? "در حال ورود…" : "ورود"}
          {!submitting && <ArrowRight size={17} />}
        </button>
      </form>
    </AuthLayout>
  );
}
