/**
 * One car on the deal board, and on the home page's shortlist.
 *
 * Shared rather than duplicated because both surfaces are showing the same
 * object and the card carries product decisions that must not drift between
 * them: the discount is measured against the peer group's own median, the peer
 * count says how many cars that median was built from, and the confidence dots
 * say whether the backend considers that enough. A 40% discount off three
 * listings is not a better deal than 12% off forty.
 *
 * `suspect` turns the ribbon amber. Past the trusted ceiling the gap is an
 * attribute the (model, trim, year) key cannot see far more often than it is a
 * bargain, and the colour has to carry that before any label is read.
 */
import { Link } from "react-router-dom";
import { AlertTriangle } from "lucide-react";
import {
  BamaLink, ConfidenceDots, Fa, Thumb, km, pct, toman,
} from "@/ui";

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
  district?: string;
  body_status?: string;
  condition_band?: string | null;
  image_url: string;
  bama_url: string;
  condition_flagged: boolean;
}

/** "۰ روز در بازار" is technically right and reads like a bug. */
export function ageLabel(days: number | null): string {
  if (days == null) return "—";
  if (days <= 0) return "امروز ثبت شده";
  return `${days} روز در بازار`;
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
  return (
    <div className="listing-card stretch-host">
      <Thumb src={deal.image_url}>
        <span className={`ribbon${suspect ? " suspect" : ""}`}>
          {pct(deal.discount_pct, 0)}
        </span>
        {deal.condition_flagged && (
          <span className="card-badges">
            <span
              className="badge warn"
              title={deal.body_status || "توضیحات آگهی به تصادف، پلاک منطقه آزاد یا وضعیت بدنه اشاره کرده است"}
            >
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
        <div className="row">
          <span className="deal-price">{toman(deal.price)}</span>
          <span>{km(deal.mileage)}</span>
          <span className="deal-median">{toman(deal.peer_median)}</span>
        </div>
        <div className="row">
          <ConfidenceDots tier={deal.confidence} />
          <span>{deal.peer_count ?? "—"} آگهی مشابه</span>
          <span>·</span>
          <span>{deal.year ?? "—"}</span>
        </div>
        <div className="row">
          <Fa>{deal.city_name || "—"}</Fa>
          {deal.district ? <><span>·</span><Fa>{deal.district}</Fa></> : null}
          <span>·</span>
          <span>{ageLabel(deal.days_listed)}</span>
        </div>
        <div className="row">
          <BamaLink href={deal.bama_url} className="ghost above-stretch" />
        </div>
      </div>
    </div>
  );
}
