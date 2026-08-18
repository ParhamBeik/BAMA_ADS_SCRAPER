/**
 * Shared presentation pieces.
 *
 * Two of these carry product decisions rather than styling:
 *
 * `Provenance` renders the envelope every research answer ships with. It is not
 * optional chrome — these numbers come from a crawl that can be incomplete, and a
 * survival curve computed across a coverage hole reads crawler downtime as cars
 * leaving the market. If the data is stale the user sees that next to the number.
 *
 * `Async` makes "we do not have enough clean data for this" a first-class result
 * rather than an error or, worse, an empty chart that looks like zero.
 */
import { useState, type ReactNode } from "react";
import { AlertTriangle, Database } from "lucide-react";
import type { Envelope } from "./api/client";

/**
 * What a cohort flag means, in the language the rest of the page is in.
 *
 * Shared because Explorer and ListingDetail both render the same flags, and the
 * detail page used to print the raw `price_outlier_low` string.
 */
export const FLAG_LABEL: Record<string, string> = {
  price_outlier_low: "خیلی زیر هم‌گروه — دلیلش را بررسی کنید",
  price_outlier_high: "خیلی بالاتر از هم‌گروه",
};

/**
 * A listing photo, or a graceful gap where one should be.
 *
 * Bama's CDN 500s on a small fraction of its own images, and a card-first layout
 * turns that into a grid of broken-image icons. `onError` drops the element so
 * the thumb's own background shows instead. Shared by both card grids because
 * they hit the same CDN.
 */
export function Thumb({ src, children }: { src?: string; children?: ReactNode }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="thumb">
      {src && !failed ? (
        <img src={src} alt="" loading="lazy" onError={() => setFailed(true)} />
      ) : (
        <div className="thumb-fallback">بدون تصویر</div>
      )}
      {children}
    </div>
  );
}

/** Persian source text inside English chrome. */
export function Fa({ children }: { children: ReactNode }) {
  return (
    <span className="fa" dir="auto">
      {children}
    </span>
  );
}

export function toman(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `${Math.round(value / 1_000_000)}M`;
  return value.toLocaleString("en-US");
}

export function pct(value: number | null | undefined, digits = 1): string {
  return value == null ? "—" : `${value.toFixed(digits)}%`;
}

/**
 * Odometer, at card density.
 *
 * Rounding to thousands rendered every new car as "۰ هزار کیلومتر", which reads
 * as missing data rather than as a zero-kilometre car — so the two are now
 * distinct strings.
 *
 * Western digits, like `toman` and `pct`. Numbers on this site are mixed into
 * lines with Latin magnitude suffixes ("3.90B") and sit in `tabular-nums`
 * columns that only align Latin figures, so Persian digits here produced lines
 * like "1404 · ۱۳۰ هزار کیلومتر" — two numeral systems in one sentence.
 */
export function km(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value === 0) return "صفر کیلومتر";
  if (value < 1000) return `${value.toLocaleString("en-US")} کیلومتر`;
  return `${Math.round(value / 1000).toLocaleString("en-US")} هزار کیلومتر`;
}

export function Card({
  title,
  children,
  action,
}: {
  title?: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="card">
      {(title || action) && (
        <header
          style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
        >
          {title && <h3 className="card-title">{title}</h3>}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "up" | "down" | "warn";
}) {
  return (
    <Card title={label}>
      <div className={`stat ${tone ?? ""}`}>{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </Card>
  );
}

export function Provenance({ envelope }: { envelope?: Partial<Envelope> }) {
  if (!envelope?.coverage) return null;
  const { coverage, as_of, methodology_version } = envelope;
  return (
    <div className="provenance">
      {/* The two facts that change how the numbers above should be read, before
          the facts about how they were computed. During the 2026-08-16 block the
          catalog was frozen for six hours and this strip still only reported a
          cheerful listing count. */}
      {coverage.source_blocked && (
        <span className="badge warn">
          <AlertTriangle size={11} /> باما فعلاً به ما پاسخ نمی‌دهد — این اعداد
          تازه نمی‌شوند
        </span>
      )}
      {coverage.removal_detection_paused && (
        <span className="badge warn">
          <AlertTriangle size={11} /> تشخیص حذف آگهی متوقف است — ممکن است آگهیِ
          فروخته‌شده هنوز فعال نشان داده شود
        </span>
      )}
      <Database size={13} />
      {coverage.complete_sweep ? (
        <span>
          {coverage.ads_covered?.toLocaleString("en-US")} آگهی، آخرین پویش{" "}
          {coverage.age_hours} ساعت پیش
        </span>
      ) : (
        <span className="warn">
          هیچ پویش کاملی ثبت نشده — این اعداد ممکن است فقط بخشی از بازار را
          پوشش دهند
        </span>
      )}
      {coverage.stale && (
        <span className="badge warn">
          <AlertTriangle size={11} /> کهنه
        </span>
      )}
      {as_of && <span>· تا {new Date(as_of).toLocaleString("fa-IR")}</span>}
      {methodology_version != null && <span>· روش نسخهٔ {methodology_version}</span>}
    </div>
  );
}

/**
 * Sample size as three dots.
 *
 * The confidence tier is the difference between "12% off, forty peers" and
 * "40% off, three peers", and it was rendered as an English word in a Persian
 * table. Filled dots are read without reading.
 */
export function ConfidenceDots({ tier }: { tier?: string | null }) {
  const filled = tier === "high" ? 3 : tier === "medium" ? 2 : tier === "low" ? 1 : 0;
  if (!filled) return <span className="dots">—</span>;
  const label = { high: "اعتماد بالا", medium: "اعتماد متوسط", low: "اعتماد کم" }[
    tier as "high" | "medium" | "low"
  ];
  return (
    <span className={`dots ${tier}`} title={label} aria-label={label}>
      {"●".repeat(filled)}
      {"○".repeat(3 - filled)}
    </span>
  );
}

/**
 * Loading, error, empty, unavailable and stale — all explicit.
 *
 * `unavailable` is the interesting one: the backend refuses to compute a number
 * from too little data and says why. Rendering that as an error would be wrong
 * (nothing failed) and rendering an empty chart would be worse (it reads as
 * zero).
 */
export function Async<T>({
  query,
  children,
  empty,
  shape = "block",
}: {
  query: { isLoading: boolean; error: unknown; data: T | undefined };
  children: (data: T) => ReactNode;
  empty?: string;
  /** Match the placeholder to what will land, so the page stops jumping. */
  shape?: "block" | "table" | "chart" | "cards";
}) {
  if (query.isLoading) {
    if (shape === "cards") {
      return (
        <div className="card-grid" aria-busy="true">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="skeleton" style={{ height: 210 }} />
          ))}
        </div>
      );
    }
    const height = shape === "table" ? 260 : shape === "chart" ? 240 : 120;
    return <div className="skeleton" style={{ height }} aria-busy="true" />;
  }
  if (query.error) {
    return (
      <div className="state error">
        {query.error instanceof Error ? query.error.message : "Something went wrong."}
      </div>
    );
  }
  const data = query.data as (T & { available?: boolean; reason?: string }) | undefined;
  if (!data) return <div className="state">{empty ?? "Nothing to show."}</div>;

  if (data.available === false) {
    return (
      <div className="state">
        <AlertTriangle size={16} />{" "}
        <strong>هنوز دادهٔ کافی و تمیز برای این محاسبه نیست.</strong>
        <div style={{ marginTop: 4 }}>{humanReason(data.reason)}</div>
      </div>
    );
  }
  return <>{children(data)}</>;
}

function humanReason(reason?: string): string {
  switch (reason) {
    case "insufficient_episodes":
      return "آگهی‌های تمام‌شدهٔ این گروه برای برآورد مدت فروش کم است.";
    case "insufficient_clean_history":
      // Not an error and not an empty cohort: removal dates recorded before the
      // crawl was reliable measured the sweep schedule rather than the market,
      // so they are excluded until enough trustworthy history accumulates.
      return "تاریخچهٔ قابل اتکا هنوز کافی نیست — مدت ماندن در بازار فقط از آگهی‌هایی شمرده می‌شود که پس از اصلاح خزشگر دیده شده‌اند.";
    case "insufficient_peers":
      return "آگهی‌های هم‌گروه برای قیمت‌گذاری این خودرو کم است.";
    case "insufficient_years":
      return "سال‌های مدلِ دارای داده برای رسم منحنی افت قیمت کافی نیست.";
    case "insufficient_listings":
      return "تعداد آگهی‌های این گروه کم است.";
    case "unknown_or_unverified_ad":
      return "این آگهی ناشناخته است یا از بررسی صحت داده رد نشده.";
    case "no_price_baseline":
      return "مبنای قیمتی قابل استفاده‌ای برای این گروه وجود ندارد.";
    default:
      return reason ?? "این گروه برای گزارش‌دادن کوچک است.";
  }
}

export function Table({
  head,
  children,
}: {
  head: ReactNode[];
  children: ReactNode;
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={i} className={i > 0 ? "num" : undefined} scope="col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
