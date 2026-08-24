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
      setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
      setPassword("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout
      eyebrow="Welcome back"
      heading="Sign in to your account"
      intro="Pick up where you left off across deals, research, and saved listings."
      footer={
        <>
          New to Bama Market? <Link to="/signup">Create an account</Link>
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
              ? { text: "Enter a valid email address", tone: "down" }
              : null
          }
        />

        <PasswordField
          id="auth-password"
          label="Password"
          value={password}
          onChange={setPassword}
          show={show}
          onToggle={() => setShow((v) => !v)}
          autoComplete="current-password"
          placeholder="Enter your password"
        />

        <button className="primary auth-submit" type="submit" disabled={!ready || submitting}>
          {submitting ? "Signing in…" : "Sign in"}
          {!submitting && <ArrowRight size={17} />}
        </button>
      </form>
    </AuthLayout>
  );
}
