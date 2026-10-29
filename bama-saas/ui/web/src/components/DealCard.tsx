/**
 * One car on the deal board, and on the home page's shortlist.
 *
 * Shared rather than duplicated because both surfaces are showing the same
 * object and the card carries product decisions that must not drift between
 * them. Four rows: title, price and mileage, year · city · age, and the deal
 * verdict, whose line also says how many fresh peers it stood on.
 *
 * `suspect` turns the ribbon amber. Past the trusted ceiling the gap is an
 * attribute the (model, trim, year) key cannot see far more often than it is a
 * bargain, and the colour has to carry that before any label is read.
 */
import { Link } from "react-router-dom";
import { AlertTriangle, BadgeCheck } from "lucide-react";
import type { Envelope } from "@/api";
import { Fa, Thumb } from "@/ui";
import { fa, km, modelYear, pct, toman, trimNote } from "@/format";

/**
 * The thresholds the deal board is standing on, so a screen can state them
 * rather than describe a filter whose value it does not know.
 */
export interface DealWindow {
  window_days: number;
  min_discount_pct: number;
  ceiling_pct: number;
  candidates: number;
  scored: number;
}

/**
 * `/api/analytics/deal-scores/`, in full.
 *
 * Declared three times before this — once in Deals, Home and Analyse — each
 * naming only the fields that page read, so one endpoint had three shapes and
 * adding a field meant finding all three. Checked against the view before
 * merging: `deal_scores` returns count, limit, offset, band, the whole window
 * and results on every request and every band, so this is the union of three
 * narrowings rather than three different contracts.
 *
 * Here rather than in `api.ts` because it is `Deal[]`, and every page already
 * imports `Deal` from this module alongside the card that renders it.
 */
export interface DealBoard extends Envelope {
  count: number;
  limit: number;
  offset: number;
  band: string;
  window: DealWindow;
  results: Deal[];
}

export interface Deal {
  code: string;
  title: string;
  discount_pct: number | null;
  price: number | null;
  peer_median: number | null;
  peer_count: number | null;
  confidence: string | null;
  age_days: number | null;
  days_listed: number | null;
  /** Band index the API ordered by; see BAND_LABEL in pages/Deals. */
  freshness: number | null;
  year: number | null;
  mileage: number | null;
  city_name: string;
  variant_name?: string;
  /** Null for a private seller. */
  dealer_id: number | null;
  district?: string;
  body_status?: string;
  condition_band?: string | null;
  image_url: string;
  bama_url: string;
  condition_flagged: boolean;
  /** How fast this model's listings leave the feed. Null — never zero — when
   *  there is not enough clean history to say; "we have not watched it long
   *  enough" and "it sells slowly" are different facts. */
  liquidity?: {
    left_pct: number;
    n: number;
    window_days: number;
  } | null;
  /** `pricing.assess_deals`: `deal`, `check` with reasons, or null when no
   *  fresh, tight peer group exists to judge against. */
  deal_status?: "deal" | "check" | null;
  deal_reasons?: string[];
  fresh_peers?: number | null;
  cheaper_than_pct?: number | null;
  /** Days since the asking price was last set (a repost at the same price
   *  keeps the original date). */
  price_age_days?: number | null;
  /** What the learned models said, or null when nothing has scored this ad. */
  ml?: {
    price_p50: number | null;
    residual_pct: number | null;
    anomaly_kind: string | null;
    sell_fast_prob: number | null;
  } | null;
}

/** Why a cheap car went to review rather than onto the board. */
export const DEAL_REASON: Record<string, string> = {
  stale_price: "قیمت قدیمی",
  atypical_mileage: "کارکرد غیرعادی",
  beyond_spread: "بسیار ارزان‌تر از حد عادی",
  suspect_misfiled: "شاید در مدل یا سال اشتباه",
  red_flags: "نکته‌ای در متن آگهی",
  not_new: "صفر کیلومتر ولی رنگ یا تعمیر دارد",
};

/** What the verdict stood on, in one line: the real comparison and how old the price is. */
export function verdictNote(
  deal: Pick<Deal, "deal_status" | "deal_reasons" | "cheaper_than_pct" | "fresh_peers" | "price_age_days">,
): string | null {
  const age = deal.price_age_days == null ? null
    : deal.price_age_days <= 0 ? "قیمت امروز" : `قیمتِ ${fa(deal.price_age_days)} روز پیش`;
  if (deal.deal_status === "check") {
    const why = (deal.deal_reasons ?? []).map((r) => DEAL_REASON[r] ?? r).join("، ");
    return [why, age].filter(Boolean).join(" · ") || null;
  }
  if (deal.deal_status === "deal" && deal.cheaper_than_pct != null) {
    return [`ارزان‌تر از ${fa(deal.cheaper_than_pct)}% از ${fa(deal.fresh_peers)} آگهی تازه`, age]
      .filter(Boolean).join(" · ");
  }
  return null;
}

/**
 * Why this listing is flagged, naming the field the flag actually came from.
 *
 * The tooltip used to say the *description* mentioned accident damage, on every
 * flagged row. That is only ever the fallback: the flag fires first on Bama's
 * structured `body_status`, which is filled on 100% of ads. So a car whose
 * description read «فروش فوری / ماشین فوق العاده سالم» was captioned as
 * describing itself as crashed — the warning was right and its stated reason
 * was false, which is the worse of the two failures.
 *
 * `condition_band` is present exactly when the structured field is what fired.
 */
export function conditionNote(deal: Pick<Deal, "body_status" | "condition_band">): string {
  if (deal.condition_band && deal.body_status) {
    return `وضعیت بدنه‌ای که فروشنده در باما ثبت کرده: «${deal.body_status}»`;
  }
  return "توضیحات آگهی به تصادف، پلاک منطقه آزاد یا وضعیت بدنه اشاره کرده است";
}

/**
 * The card is one big click target that also contains a link out to bama.ir.
 *
 * Wrapping the whole card in a `<Link>` made that an `<a>` inside an `<a>`,
 * which is invalid and which browsers repair by splitting the outer element —
 * so the markup the user actually got was not the markup here. The stretched-
 * link pattern fixes it: the card is a plain container, only the title is a real
 * link, and its ::after covers the card. The outbound link sits above that
 * overlay, so both targets work and there is exactly one link per destination
 * for a screen reader to announce.
 */
export function DealCard({ deal, suspect }: { deal: Deal; suspect: boolean }) {
  // Four rows. The peer median, sample size, days on Bama, liquidity, the Bama
  // link and the model's note are on the listing page one tap away; on the card they made
  // eight rows, and a struck-through median read as the car's old price.
  const note = verdictNote(deal);
  return (
    <div className="listing-card stretch-host">
      <Thumb src={deal.image_url}>
        <span className={`ribbon${suspect ? " suspect" : ""}`}>
          {pct(deal.discount_pct, 0)}
        </span>
        {deal.condition_flagged && (
          <span className="card-badges">
            <span className="badge warn" title={conditionNote(deal)}>
              <AlertTriangle size={11} /> {deal.body_status || "وضعیت بدنه"}
            </span>
          </span>
        )}
      </Thumb>
      <div className="listing-meta">
        <strong>
          <Link to={`/listing/${deal.code}`} className="stretch-link">
            <Fa>{deal.title || deal.code}</Fa>
          </Link>
        </strong>
        {trimNote(deal.title, deal.variant_name) && (
          <span className="stat-sub"><Fa>{trimNote(deal.title, deal.variant_name)}</Fa></span>
        )}
        <div className="row">
          <span className="deal-price">{toman(deal.price)}</span>
          <span>{km(deal.mileage)}</span>
        </div>
        <div className="row card-facts">
          <span>{modelYear(deal.year)}</span>
          <span aria-hidden>·</span>
          <Fa>{deal.city_name || "—"}</Fa>
        </div>
        {/* The verdict line carries the price's age, which is the age that
            matters; how long the ad has sat on Bama is on the listing page. */}
        {note && (
          <p className="stat-sub" style={{ margin: 0 }}>
            {deal.deal_status === "check"
              ? <AlertTriangle size={11} className="inline align-[-1px]" />
              : <BadgeCheck size={11} className="inline align-[-1px]" />}{" "}
            {note}
          </p>
        )}
      </div>
    </div>
  );
}

/** At most one car per dealer. One dealer's 0 km listings took 4 of the 6
 *  cards (twice the same L8), which is one seller's stock, not a market. */
export function onePerSeller<T extends { dealer_id: number | null; model_name?: string | null; year?: number | null }>(
  rows: T[], n: number,
): T[] {
  const seen = new Set<string>();
  // One per seller, and one per car: two «پژو 206 · 1384» at the same price
  // from different cities filled a six-slot strip with the same answer twice.
  return rows.filter((r) => {
    const keys = [r.dealer_id != null ? `d${r.dealer_id}` : null,
                  r.model_name ? `c${r.model_name}|${r.year ?? ""}` : null].filter(Boolean) as string[];
    if (keys.some((k) => seen.has(k))) return false;
    keys.forEach((k) => seen.add(k));
    return true;
  }).slice(0, n);
}
