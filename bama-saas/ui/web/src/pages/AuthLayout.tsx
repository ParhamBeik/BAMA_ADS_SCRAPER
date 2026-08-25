/**
 * The shell and the field primitives both auth screens share.
 *
 * Sign-in and sign-up are separate routes rather than one screen with a toggle:
 * they are different intents, the browser back button should distinguish them,
 * and "send me the signup link" should be a URL. What they genuinely share —
 * the layout, the password field, the rules — lives here.
 *
 * The password rules mirror Django's AUTH_PASSWORD_VALIDATORS exactly (see
 * config/settings.py). Anything the form accepts, the server accepts; the two
 * checks it cannot do locally (common-password list, similarity to the email)
 * stay server-side and surface as a normal form error.
 */
import { type ReactNode } from "react";
import { Eye, EyeOff } from "lucide-react";
import { BrandMark } from "../ui";

export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export interface Check {
  id: string;
  label: string;
  ok: boolean;
}

/**
 * Django's MinimumLengthValidator and NumericPasswordValidator, in the browser.
 *
 * These two and no more, because these gate the submit button. A third
 * "mixes letters with a number or symbol" rule used to live here and had no
 * counterpart in AUTH_PASSWORD_VALIDATORS, so the form refused passwords the
 * server would happily have taken — a long all-letters passphrase, which is a
 * good password, could not be submitted at all. Character-class variety now
 * feeds the strength meter only, where advice belongs.
 */
export function passwordChecks(password: string): Check[] {
  return [
    { id: "length", label: "دست‌کم ۸ نویسه", ok: password.length >= 8 },
    {
      id: "not-numeric",
      label: "فقط عدد نباشد",
      ok: Boolean(password) && !/^\d+$/.test(password),
    },
  ];
}

/** Advisory only — never gates submission. */
export function strengthOf(password: string, checks: Check[]) {
  if (!password) return null;
  const required = checks.filter((c) => c.ok).length === checks.length;
  const roomy = password.length >= 12;
  const varied = /[a-zA-Z]/.test(password) && /[\d\W]/.test(password);
  if (!required) return { label: "ضعیف", tone: "down" as const, pct: 33 };
  // Both, not either: "aaaaaaaaaaaa" is long and "Pa55!" is varied, and calling
  // either one Strong tells the user something untrue about their password.
  if (roomy && varied) return { label: "قوی", tone: "up" as const, pct: 100 };
  return { label: "خوب", tone: "warn" as const, pct: 66 };
}

export function AuthLayout({
  eyebrow,
  heading,
  intro,
  children,
  footer,
}: {
  eyebrow: string;
  heading: string;
  intro: string;
  children: ReactNode;
  footer: ReactNode;
}) {
  return (
    <main className="auth-shell">
      <section className="auth-visual" aria-hidden="true">
        <div className="auth-brand">
          <BrandMark size={30} />
          <span>بازار خودرو باما</span>
        </div>
        <div className="auth-visual-copy">
          <p className="auth-eyebrow">تحلیل بازار، بدون نویز</p>
          <h1>آگهی‌هایی که ارزش وقت شما را دارند.</h1>
          <p>
            سیگنال‌های تمیز بازار را دنبال کنید، قیمت منصفانه را بسنجید و از
            جست‌وجو با اطمینان به تصمیم برسید.
          </p>
        </div>
        <dl className="auth-facts">
          <div>
            <dt>آگهی زیر نظر</dt>
            <dd>+۳۴٬۰۰۰</dd>
          </div>
          <div>
            <dt>به‌روزرسانی</dt>
            <dd>هر ۱۵ دقیقه</dd>
          </div>
          <div>
            <dt>تاریخچه قیمت</dt>
            <dd>برای هر آگهی</dd>
          </div>
        </dl>
      </section>

      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-card-inner">
          <div className="auth-brand auth-brand-compact">
            <BrandMark size={24} />
            <span>بازار خودرو باما</span>
          </div>
          <div className="auth-card-header">
            <p className="auth-eyebrow">{eyebrow}</p>
            <h2 id="auth-title">{heading}</h2>
            <p>{intro}</p>
          </div>
          {children}
          <p className="auth-footer">{footer}</p>
        </div>
      </section>
    </main>
  );
}

export function EmailField({
  value,
  onChange,
  onBlur,
  touched,
  hint,
}: {
  value: string;
  onChange: (v: string) => void;
  onBlur: () => void;
  touched: boolean;
  /** Shown under the field once touched — availability, or a format complaint. */
  hint?: { text: string; tone: "up" | "down" | "muted" } | null;
}) {
  // Only complain once the field has been left. Validating on every keystroke
  // marks "p@" invalid while the user is still typing their own address.
  const invalid = touched && Boolean(value) && !EMAIL_RE.test(value.trim());
  return (
    <label className="auth-field" htmlFor="auth-email">
      <span>نشانی ایمیل</span>
      <input
        id="auth-email"
        type="email"
        autoComplete="username"
        autoFocus
        required
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        placeholder="you@example.com"
        aria-invalid={invalid}
        aria-describedby={hint ? "auth-email-hint" : undefined}
      />
      {touched && hint && (
        <small id="auth-email-hint" className={hint.tone}>
          {hint.text}
        </small>
      )}
    </label>
  );
}

export function PasswordField({
  id,
  label,
  value,
  onChange,
  show,
  onToggle,
  autoComplete,
  placeholder,
  describedBy,
  invalid,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  show: boolean;
  onToggle: () => void;
  autoComplete: string;
  placeholder: string;
  describedBy?: string;
  invalid?: boolean;
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
          aria-invalid={invalid}
          aria-describedby={describedBy}
        />
        <button
          className="auth-icon-button"
          type="button"
          onClick={onToggle}
          // The label states the action, not the state: a screen reader user
          // pressing this needs to know what happens, not what is.
          aria-label={show ? `پنهان کردن ${label}` : `نمایش ${label}`}
          aria-pressed={show}
        >
          {show ? <EyeOff size={17} /> : <Eye size={17} />}
        </button>
      </div>
    </label>
  );
}

export function Requirements({ checks, id }: { checks: Check[]; id: string }) {
  return (
    <div className="auth-requirements" id={id}>
      <strong>شرایط گذرواژه</strong>
      <ul>
        {checks.map((check) => (
          <li key={check.id} className={check.ok ? "complete" : ""}>
            <span aria-hidden="true">{check.ok ? "✓" : "○"}</span>
            {check.label}
            <span className="sr-only">{check.ok ? " — رعایت شده" : " — هنوز رعایت نشده"}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function FormError({ message }: { message: string | null }) {
  return (
    <div className="auth-error-slot" role="alert" aria-live="polite">
      {message && <div className="auth-error">{message}</div>}
    </div>
  );
}
