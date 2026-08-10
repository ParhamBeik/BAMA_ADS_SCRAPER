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
import type { ReactNode } from "react";
import { AlertTriangle, Database, Info } from "lucide-react";
import type { Envelope } from "./api/client";
import { ApiError } from "./api/client";

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
      <Database size={13} />
      {coverage.complete_sweep ? (
        <span>
          {coverage.ads_covered?.toLocaleString("en-US")} listings, swept{" "}
          {coverage.age_hours}h ago
        </span>
      ) : (
        <span className="warn">
          No completed crawl on record — figures may cover only part of the market
        </span>
      )}
      {coverage.stale && (
        <span className="badge warn">
          <AlertTriangle size={11} /> Stale
        </span>
      )}
      {as_of && <span>· as of {new Date(as_of).toLocaleString("en-GB")}</span>}
      {methodology_version != null && <span>· method v{methodology_version}</span>}
    </div>
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
}: {
  query: { isLoading: boolean; error: unknown; data: T | undefined };
  children: (data: T) => ReactNode;
  empty?: string;
}) {
  if (query.isLoading) {
    return <div className="skeleton" style={{ height: 120 }} aria-busy="true" />;
  }
  if (query.error) {
    const err = query.error;
    if (err instanceof ApiError && err.isSubscriptionRequired) {
      return (
        <div className="state">
          <Info size={16} /> <strong>Research plan required.</strong>
          <div>Cohort analytics are part of the research tier.</div>
        </div>
      );
    }
    if (err instanceof ApiError && err.isAuthRequired) {
      return <div className="state">Sign in to view this.</div>;
    }
    return (
      <div className="state error">
        {err instanceof Error ? err.message : "Something went wrong."}
      </div>
    );
  }
  const data = query.data as (T & { available?: boolean; reason?: string }) | undefined;
  if (!data) return <div className="state">{empty ?? "Nothing to show."}</div>;

  if (data.available === false) {
    return (
      <div className="state">
        <AlertTriangle size={16} />{" "}
        <strong>Not enough clean data for this yet.</strong>
        <div style={{ marginTop: 4 }}>{humanReason(data.reason)}</div>
      </div>
    );
  }
  return <>{children(data)}</>;
}

function humanReason(reason?: string): string {
  switch (reason) {
    case "insufficient_episodes":
      return "Too few completed listings in this cohort to estimate time on market.";
    case "insufficient_peers":
      return "Too few comparable listings to price this against.";
    case "insufficient_years":
      return "Not enough model years with data to draw a depreciation curve.";
    case "insufficient_listings":
      return "Too few listings in this cohort.";
    case "unknown_or_unverified_ad":
      return "That listing is unknown, or failed data verification.";
    case "no_price_baseline":
      return "No usable price baseline for this cohort.";
    default:
      return reason ?? "The cohort is too small to report on.";
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
