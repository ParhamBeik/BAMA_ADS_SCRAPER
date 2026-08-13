import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Async, Fa, Table, toman } from "../ui";
import { ListingActions } from "../engagement";

interface Deal {
  code: string;
  title: string;
  score: number;
  discount_pct: number;
  peer_median: number;
  price: number | null;
  brand_name?: string;
  model_name?: string;
}

export function Deals() {
  const deals = useQuery({
    queryKey: ["deal-scores"],
    queryFn: ({ signal }) =>
      api.get<{ results?: Deal[] } | Deal[]>("/api/analytics/deal-scores/?limit=50", signal),
  });

  return (
    <div className="stack" dir="rtl">
      <p className="muted">آگهی‌هایی که زیر میانهٔ هم‌گروه خود قیمت خورده‌اند.</p>
      <Async query={deals} empty="هنوز امتیازی محاسبه نشده.">
        {(body) => {
          const rows = Array.isArray(body) ? body : body.results ?? [];
          if (!rows.length) return <div className="state">هنوز امتیازی محاسبه نشده.</div>;
          return (
            <Table head={["آگهی", "تخفیف", "امتیاز", "قیمت", ""]}>
              {rows.map((d) => (
                <tr key={d.code}>
                  <td>
                    <Link to={`/listing/${d.code}`}>
                      <Fa>{d.title || d.code}</Fa>
                    </Link>
                  </td>
                  <td className="num">{d.discount_pct?.toFixed?.(1) ?? d.discount_pct}٪</td>
                  <td className="num">{d.score}</td>
                  <td className="num">{toman(d.price)}</td>
                  <td><ListingActions code={d.code} /></td>
                </tr>
              ))}
            </Table>
          );
        }}
      </Async>
    </div>
  );
}
