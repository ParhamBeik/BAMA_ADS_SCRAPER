/**
 * Market Overview — headline counts plus the composition-controlled index.
 *
 * The index, not the raw median, is the number that answers "did prices move".
 * A request of 30 days is clamped to whatever history actually exists; the
 * window on the card is that real span, not the number we asked for.
 */
import { lazy, Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Envelope } from "../api/client";
import { Async, Card, Fa, Provenance, Stat, Table, pct } from "../ui";

const Chart = lazy(() => import("../Chart").then((m) => ({ default: m.Chart })));

interface OverviewData extends Envelope {
  active_listings: number;
  priced_listings: number;
  brands: number;
  models: number;
  top_brands: { brand__name_fa: string; n: number }[];
}

interface IndexPoint {
  date: string;
  index_value: number | null;
  return_pct: number | null;
  cohort_count: number;
  ad_count: number;
}

interface MarketIndex extends Partial<Envelope> {
  scope: string;
  base_value: number;
  latest_index: number | null;
  change_pct: number | null;
  window: {
    requested_days: number;
    days: number;
    clamped: boolean;
    first_date: string | null;
    last_date: string | null;
  };
  series: IndexPoint[];
}

function windowLabel(w: MarketIndex["window"]): string {
  const span =
    w.first_date && w.last_date
      ? `${w.first_date} → ${w.last_date}`
      : "no dates yet";
  const asked = `${w.days} of ${w.requested_days} days`;
  return w.clamped
    ? `${span} · ${asked} (history is shorter than requested)`
    : `${span} · ${asked}`;
}

export function Overview() {
  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: ({ signal }) => api.get<OverviewData>("/api/analytics/overview/", signal),
  });

  const index = useQuery({
    queryKey: ["market-index", 30],
    queryFn: ({ signal }) =>
      api.get<MarketIndex>("/api/analytics/market-index/?days=30", signal),
  });

  return (
    <div className="stack" dir="rtl">
      <Async query={overview}>
        {(data) => (
          <>
            <div className="grid cols-4">
              <Stat
                label="آگهی فعال"
                value={data.active_listings.toLocaleString("en-US")}
                sub={`${data.priced_listings.toLocaleString("en-US")} با قیمت`}
              />
              <Stat label="برند" value={data.brands} />
              <Stat label="مدل" value={data.models} />
              <Stat
                label="با قیمت"
                value={data.priced_listings.toLocaleString("en-US")}
                sub="از آگهی‌های فعال"
              />
            </div>
            <Card title="بزرگ‌ترین برندها">
              <Table head={["برند", "آگهی"]}>
                {data.top_brands.map((b) => (
                  <tr key={b.brand__name_fa}>
                    <td>
                      <Fa>{b.brand__name_fa}</Fa>
                    </td>
                    <td className="num">{b.n.toLocaleString("en-US")}</td>
                  </tr>
                ))}
              </Table>
              <Provenance envelope={data} />
            </Card>
          </>
        )}
      </Async>

      <Card title="شاخص قیمت کنترل‌شده">
        <p className="stat-sub" style={{ marginTop: 0 }}>
          هر گروه فقط با خودش مقایسه می‌شود تا تغییر ترکیب آگهی‌ها شبیه تغییر
          قیمت به نظر نرسد.
        </p>
        <Async query={index} empty="هنوز تاریخچهٔ شاخصی نیست.">
          {(data) => {
            const points = data.series ?? [];
            return (
              <>
                <p className="stat-sub">بازهٔ واقعی: {windowLabel(data.window)}</p>
                <div className="grid cols-2" style={{ marginBottom: 10 }}>
                  <Stat
                    label="شاخص"
                    value={
                      data.latest_index != null ? data.latest_index.toFixed(1) : "—"
                    }
                    sub={`پایه ${data.base_value}`}
                  />
                  <Stat
                    label="تغییر بازه"
                    value={pct(data.change_pct)}
                    tone={
                      data.change_pct == null
                        ? undefined
                        : data.change_pct >= 0
                          ? "up"
                          : "down"
                    }
                  />
                </div>
                {points.length ? (
                  <Suspense fallback={<p className="muted">…</p>}>
                    <Chart
                      x={points.map((p) => p.date)}
                      series={[
                        {
                          name: "Index",
                          data: points.map((p) => p.index_value),
                          area: true,
                        },
                      ]}
                      yFormatter={(v) => v.toFixed(1)}
                    />
                  </Suspense>
                ) : (
                  <div className="state">هنوز تاریخچهٔ شاخصی نیست.</div>
                )}
                <Provenance envelope={data} />
              </>
            );
          }}
        </Async>
      </Card>
    </div>
  );
}
