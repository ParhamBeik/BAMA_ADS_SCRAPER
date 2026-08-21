import { useMemo, useState, type FormEvent } from "react";
import { ArrowRight, Eye, EyeOff, ShieldCheck } from "lucide-react";
import { useAuth } from "../auth";
import { BrandMark } from "../ui";

type Mode = "login" | "signup";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function passwordChecks(password: string, confirmation: string, signup: boolean) {
  const checks = [
    { id: "length", label: "At least 8 characters", ok: password.length >= 8 },
    {
      id: "case",
      label: "Uppercase and lowercase letters",
      ok: /[a-z]/.test(password) && /[A-Z]/.test(password),
    },
    {
      id: "number",
      label: "A number or special character",
      ok: /\d/.test(password) || /[^\w\s]/.test(password),
    },
    { id: "not-digits", label: "Not only numbers", ok: Boolean(password) && !/^\d+$/.test(password) },
  ];
  if (signup) checks.push({ id: "match", label: "Passwords match", ok: Boolean(password && password === confirmation) });
  return checks;
}

function PasswordField({
  id,
  label,
  value,
  onChange,
  show,
  onToggle,
  autoComplete,
  placeholder,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  show: boolean;
  onToggle: () => void;
  autoComplete: string;
  placeholder: string;
}) {
  return (
    <label className="auth-field" htmlFor={id}>
      <span>{label}</span>
      <div className="auth-input-wrap">
        <input
          id={id}
          type={show ? "text" : "password"}
          required
          autoComplete={autoComplete}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
        />
        <button
          className="auth-icon-button"
          type="button"
          onClick={onToggle}
          aria-label={show ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
        >
          {show ? <EyeOff size={17} /> : <Eye size={17} />}
        </button>
      </div>
    </label>
  );
}

export function Login() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmation, setShowConfirmation] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const signup = mode === "signup";
  const emailValid = EMAIL_RE.test(email.trim());
  const checks = useMemo(
    () => passwordChecks(password, confirmation, signup),
    [password, confirmation, signup],
  );
  const passwordValid = checks.every((check) => check.ok);
  const ready = emailValid && (signup ? passwordValid : Boolean(password));
  const passedPasswordChecks = checks.filter((check) => check.id !== "match" && check.ok).length;
  const strength = !password
    ? null
    : passedPasswordChecks <= 1
      ? { label: "Weak", tone: "down" }
      : passedPasswordChecks < 4
        ? { label: "Good", tone: "warn" }
        : { label: "Strong", tone: "up" };

  function switchMode(next: Mode) {
    setMode(next);
    setError(null);
    setPassword("");
    setConfirmation("");
    setShowPassword(false);
    setShowConfirmation(false);
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!ready || submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      if (signup) await register(email.trim(), password);
      else await login(email.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-visual" aria-label="Bama Market">
        <div className="auth-brand">
          <BrandMark size={30} />
          <span>Bama Market</span>
        </div>
        <div className="auth-visual-copy">
          <p className="auth-eyebrow">Market intelligence, without the noise</p>
          <h1>Find the listings worth your attention.</h1>
          <p>
            Track clean market signals, compare fair prices, and move from search
            to decision with confidence.
          </p>
        </div>
        <div className="auth-trust">
          <ShieldCheck size={17} />
          <span>Your session is protected with secure Django authentication.</span>
        </div>
      </section>

      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-card-header">
          <p className="auth-eyebrow">{signup ? "Start with Bama Market" : "Welcome back"}</p>
          <h2 id="auth-title">{signup ? "Create your account" : "Sign in to your account"}</h2>
          <p>
            {signup
              ? "Save the listings that matter and return to the market with everything in one place."
              : "Pick up where you left off across deals, research, and saved listings."}
          </p>
        </div>

        <div className="auth-tabs" role="tablist" aria-label="Authentication mode">
          <button
            type="button"
            role="tab"
            aria-selected={!signup}
            className={!signup ? "active" : ""}
            onClick={() => switchMode("login")}
          >
            Sign in
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={signup}
            className={signup ? "active" : ""}
            onClick={() => switchMode("signup")}
          >
            Create account
          </button>
        </div>

        <form className="auth-form" onSubmit={onSubmit} noValidate>
          {error && (
            <div className="auth-error" role="alert" aria-live="polite">
              {error}
            </div>
          )}

          <label className="auth-field" htmlFor="auth-email">
            <span>Email address</span>
            <input
              id="auth-email"
              type="email"
              autoComplete="username"
              autoFocus
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              aria-invalid={Boolean(email) && !emailValid}
            />
            {email && <small className={emailValid ? "up" : "down"}>{emailValid ? "Valid email" : "Enter a valid email"}</small>}
          </label>

          <PasswordField
            id="auth-password"
            label="Password"
            value={password}
            onChange={setPassword}
            show={showPassword}
            onToggle={() => setShowPassword((visible) => !visible)}
            autoComplete={signup ? "new-password" : "current-password"}
            placeholder={signup ? "Create a password" : "Enter your password"}
          />

          {signup && password && (
            <div className="auth-strength" aria-label={`Password strength: ${strength?.label}`}>
              <div className={`auth-strength-bar ${strength?.tone}`} style={{ width: `${Math.max(12, passedPasswordChecks * 25)}%` }} />
              <span className={strength?.tone}>{strength?.label}</span>
            </div>
          )}

          {signup && (
            <>
              <PasswordField
                id="auth-confirm-password"
                label="Confirm password"
                value={confirmation}
                onChange={setConfirmation}
                show={showConfirmation}
                onToggle={() => setShowConfirmation((visible) => !visible)}
                autoComplete="new-password"
                placeholder="Re-enter your password"
              />
              <div className="auth-requirements">
                <strong>Password requirements</strong>
                <ul>
                  {checks.map((check) => (
                    <li key={check.id} className={check.ok ? "complete" : ""}>
                      <span aria-hidden="true">{check.ok ? "✓" : "○"}</span>
                      {check.label}
                    </li>
                  ))}
                </ul>
              </div>
            </>
          )}

          <button className="primary auth-submit" type="submit" disabled={!ready || submitting}>
            {submitting ? "Working…" : signup ? "Create account" : "Sign in"}
            {!submitting && <ArrowRight size={17} />}
          </button>
        </form>

        <p className="auth-footer">
          {signup ? "Already have an account?" : "New to Bama Market?"}{" "}
          <button type="button" onClick={() => switchMode(signup ? "login" : "signup")}>
            {signup ? "Sign in" : "Create an account"}
          </button>
        </p>
      </section>
    </main>
  );
}
