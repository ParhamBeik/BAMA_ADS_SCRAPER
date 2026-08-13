/**
 * The deal board — the product's point.
 *
 * Every column here exists so the ranking can be checked rather than trusted.
 * The discount is measured against the cohort's own median, the peer count says
 * how many cars that median was built from, and the confidence tier says whether
 * the backend considers that enough. A 40% discount off three listings is not a
 * better deal than 12% off forty, and the table has to make that visible.
 *
 * Rows are ranked by discount alone. The old score multiplied the discount by
 * `exp(-age/90)`, which mixed an uncalibrated freshness half-life into a number
 * labelled "deal", so age is now its own column and nothing is weighted by it.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Envelope } from "../api/client";
import { Async, Card, Fa, Provenance, Table, pct, toman } from "../ui";
import { ListingActions } from "../engagement";

interface Deal {
  code: string;
  title: string;
  discount_pct: number | null;
  price: number | null;
  peer_median: number | null;
  peer_count: number | null;
  confidence: string | null;
  age_days: number | null;
}

interface DealBoard extends Envelope {
  results: Deal[];
}

/** high / medium / low from fair_price's tier, mapped onto the badge palette. */
function confidenceBadge(tier: string | null) {
  if (!tier) return <span className="badge">—</span>;
  const tone = tier === "high" ? "ok" : tier === "low" ? "warn" : "accent";
  return <span className={`badge ${tone}`}>{tier}</span>;
}

export function Deals() {
  const deals = useQuery({
    queryKey: ["deal-scores"],
    queryFn: ({ signal }) =>
      api.get<DealBoard>("/api/analytics/deal-scores/?limit=50", signal),
  });

  return (
    <div className="stack" dir="rtl">
      <p className="muted">
        آگهی‌هایی که زیر میانهٔ هم‌گروه خود قیمت خورده‌اند. تخفیف نسبت به میانهٔ
        هم‌گروه محاسبه می‌شود؛ «هم‌گروه» و «اعتماد» نشان می‌دهند این میانه از چند
        آگهی ساخته شده است.
      </p>
      <Card>
        <Async query={deals} empty="هنوز امتیازی محاسبه نشده.">
          {(board) => {
            const rows = board.results ?? [];
            if (!rows.length) {
              return <div className="state">هنوز امتیازی محاسبه نشده.</div>;
            }
            return (
              <>
                <Table
                  head={[
                    "آگهی", "تخفیف", "قیمت", "میانهٔ هم‌گروه",
                    "هم‌گروه", "اعتماد", "عمر آگهی", "",
                  ]}
                >
                  {rows.map((d) => (
                    <tr key={d.code}>
                      <td>
                        <Link to={`/listing/${d.code}`}>
                          <Fa>{d.title || d.code}</Fa>
                        </Link>
                      </td>
                      <td className="num up">{pct(d.discount_pct)}</td>
                      <td className="num">{toman(d.price)}</td>
                      <td className="num">{toman(d.peer_median)}</td>
                      <td className="num">{d.peer_count ?? "—"}</td>
                      <td className="num">{confidenceBadge(d.confidence)}</td>
                      <td className="num">
                        {d.age_days != null ? `${d.age_days}d` : "—"}
                      </td>
                      <td className="num">
                        <ListingActions code={d.code} />
                      </td>
                    </tr>
                  ))}
                </Table>
                <Provenance envelope={board} />
              </>
            );
          }}
        </Async>
      </Card>
    </div>
  );
}
