import { useEffect, useMemo, useState, type FormEvent } from "react";
import { ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import {
  AuthLayout,
  EMAIL_RE,
  EmailField,
  FormError,
  PasswordField,
  Requirements,
  passwordChecks,
  strengthOf,
} from "./AuthLayout";

export function Signup() {
  const { register } = useAuth();
  const [email, setEmail] = useState("");
  const [emailTouched, setEmailTouched] = useState(false);
  const [taken, setTaken] = useState<boolean | null>(null);
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmation, setShowConfirmation] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const trimmed = email.trim();
  const emailValid = EMAIL_RE.test(trimmed);
  const checks = useMemo(() => passwordChecks(password), [password]);
  const passwordValid = checks.every((c) => c.ok);
  const matches = Boolean(password) && password === confirmation;
  const ready = emailValid && taken !== true && passwordValid && matches;
  const strength = strengthOf(password, checks);

  // Tell the user the address is taken while they are still on that field,
  // instead of after they have chosen a password and pressed submit. Debounced
  // so it is one request per pause, not one per keystroke. The server still owns
  // the decision — this is a courtesy, and a race here costs a form error.
  useEffect(() => {
    if (!emailValid) {
      setTaken(null);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api
        .get<{ available: boolean }>(
          `/api/auth/email-available/?email=${encodeURIComponent(trimmed)}`,
          controller.signal,
        )
        .then((r) => setTaken(!r.available))
        .catch(() => setTaken(null));
    }, 400);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [trimmed, emailValid]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!ready || submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      await register(trimmed, password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  const emailHint = !email
    ? null
    : !emailValid
      ? { text: "Enter a valid email address", tone: "down" as const }
      : taken === true
        ? { text: "An account with this email already exists", tone: "down" as const }
        : taken === false
          ? { text: "This email is available", tone: "up" as const }
          : null;

  return (
    <AuthLayout
      eyebrow="Start with Bama Market"
      heading="Create your account"
      intro="Save the listings that matter and return to the market with everything in one place."
      footer={
        <>
          Already have an account? <Link to="/login">Sign in</Link>
        </>
      }
    >
      <form className="auth-form" onSubmit={onSubmit} noValidate>
        <FormError message={error} />

        <EmailField
          value={email}
          onChange={setEmail}
          onBlur={() => setEmailTouched(true)}
          touched={emailTouched || Boolean(email)}
          hint={emailHint}
        />

        <PasswordField
          id="auth-password"
          label="Password"
          value={password}
          onChange={setPassword}
          show={showPassword}
          onToggle={() => setShowPassword((v) => !v)}
          autoComplete="new-password"
          placeholder="Create a password"
          describedBy="auth-requirements"
          invalid={Boolean(password) && !passwordValid}
        />

        {password && strength && (
          <div className="auth-strength">
            <div className={`auth-strength-bar ${strength.tone}`} style={{ width: `${strength.pct}%` }} />
            <span className={strength.tone}>{strength.label}</span>
          </div>
        )}

        <Requirements checks={checks} id="auth-requirements" />

        <PasswordField
          id="auth-confirm-password"
          label="Confirm password"
          value={confirmation}
          onChange={setConfirmation}
          show={showConfirmation}
          onToggle={() => setShowConfirmation((v) => !v)}
          autoComplete="new-password"
          placeholder="Re-enter your password"
          invalid={Boolean(confirmation) && !matches}
          describedBy="auth-confirm-hint"
        />
        {confirmation && !matches && (
          <small id="auth-confirm-hint" className="down auth-inline-hint">
            Passwords do not match
          </small>
        )}

        <button className="primary auth-submit" type="submit" disabled={!ready || submitting}>
          {submitting ? "Creating account…" : "Create account"}
          {!submitting && <ArrowRight size={17} />}
        </button>
      </form>
    </AuthLayout>
  );
}
