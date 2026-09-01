/**
 * What a budget actually buys.
 *
 * Every other screen in this app runs from a car to its price. This one runs
 * the other way, which is how most people arrive: they know what they can
 * spend and not what it buys. Until this existed the closest thing was
 * `/explore` with a price filter, which answers a different question — it
 * returns *listings*, so a reader learned that 47 cars matched and nothing
 * about which models were within reach.
 *
 * Three decisions worth knowing about.
 *
 * **The unit is the cohort, not the listing.** "A 1398 Peugeot 207 automatic"
 * is a decision someone can make; "listing ad7f2" is not.
 *
 * **Ranked by reach, not by cheapness.** How much of a cohort the budget clears
 * is the useful ordering — a budget that buys the best-kept 80% of one model is
 * a better suggestion than one scraping the bottom 5% of a dearer one, and
 * sorting by price puts exactly the wrong cars first.
 *
 * **Tolerance is explicit and separate.** A car 2% over budget is not invisible;
 * it is shown, and the count of what is genuinely at-or-under the number is
 * reported beside the count of what is merely within reach. Those are different
 * facts and collapsing them would overstate what the money does.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Wallet } from "lucide-react";
import { api } from "../api";
import type { Envelope } from "../api";
import { qs, useFilters } from "../filters";
import { Async, Card, Fa, Provenance, Stat, Table, fa, km, pct, toman } from "../ui";
import { Button } from "../components/ui/button";

interface Option {
  model_id: number;
  variant_id: number | null;
  year_jalali: number;
  name: string;
  brand_name: string;
  brand_slug: string;
  variant_name: string;
  n: number;
  within_budget: number;
  cohort_size: number;
  reach_pct: number;
  median_price: number;
  cheapest: number;
  median_mileage: number | null;
}

interface Affordable extends Partial<Envelope> {
  budget: number;
  tolerance_pct: number;
  ceiling: number;
  cohorts_matched: number;
  listings_matched: number;
  options: Option[];
}

const BILLION = 1_000_000_000;

/** Round numbers people actually think in, so the common case is one click. */
const PRESETS: [string, number][] = [
  ["۳۰۰ میلیون", 300_000_000],
  ["۵۰۰ میلیون", 500_000_000],
  ["۱ میلیارد", BILLION],
  ["۲ میلیارد", 2 * BILLION],
  ["۵ میلیارد", 5 * BILLION],
];

const TOLERANCES = [0, 5, 10, 20];

export function Budget() {
  const filters = useFilters();
  const budget = filters.getInt("budget");
  const tolerance = filters.getInt("tolerance") ?? 10;
  const [draft, setDraft] = useState(budget ? String(budget) : "");

  const result = useQuery({
    queryKey: ["affordable", budget, tolerance],
    enabled: Boolean(budget),
    queryFn: ({ signal }) =>
      api.get<Affordable>(
        `/api/analytics/affordable/${qs({ budget, tolerance })}`, signal),
  });

  const commit = (value: string) => {
    const n = Number(value.replace(/[^\d]/g, ""));
    filters.set({ budget: Number.isFinite(n) && n > 0 ? n : null });
  };

  return (
    <div className="stack">
      <div>
        <h1 style={{ margin: 0, fontSize: 22, letterSpacing: "-0.01em" }}>
          با بودجه‌ام چه می‌توانم بخرم؟
        </h1>
        <p className="stat-sub" style={{ margin: 0 }}>
          مبلغی که در نظر دارید را بنویسید تا ببینید کدام خودروها در دسترس‌اند.
        </p>
      </div>

      <Card title="بودجه شما">
        <form
          className="row"
          style={{ gap: 8, flexWrap: "wrap" }}
          onSubmit={(e) => {
            e.preventDefault();
            commit(draft);
          }}
        >
          <Wallet className="text-muted-foreground size-4 flex-none" aria-hidden />
          <input
            className="border-border bg-panel min-w-0 flex-1 rounded-md border px-3 py-2 text-sm sm:max-w-64"
            type="number"
            inputMode="numeric"
            aria-label="بودجه به تومان"
            placeholder="مثلاً ۱۰۰۰۰۰۰۰۰۰"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={(e) => commit(e.target.value)}
          />
          <Button type="submit">ببین چه می‌شود خرید</Button>
        </form>

        <div className="chips mt-3">
          {PRESETS.map(([label, value]) => (
            <button
              key={label}
              type="button"
              className={`preset${budget === value ? " on" : ""}`}
              aria-pressed={budget === value}
              onClick={() => {
                setDraft(String(value));
                filters.set({ budget: value });
              }}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="row mt-3" style={{ gap: 8, flexWrap: "wrap" }}>
          <span className="stat-sub">چقدر بالاتر از بودجه هم قابل بررسی است؟</span>
          {TOLERANCES.map((t) => (
            <button
              key={t}
              type="button"
              className={`preset${tolerance === t ? " on" : ""}`}
              aria-pressed={tolerance === t}
              onClick={() => filters.set({ tolerance: t })}
            >
              {t === 0 ? "دقیقاً همین مبلغ" : `تا ${fa(t)}٪ بیشتر`}
            </button>
          ))}
        </div>
      </Card>

      {!budget ? (
        <Card>
          <div className="state">
            <strong>مبلغی وارد کنید.</strong>
            <p className="empty-hint">
              نتیجه بر پایه آگهی‌های فعال و قیمت‌دار است — همان آگهی‌هایی که در
              بخش جست‌وجو می‌بینید. آگهی‌های اقساطی کنار گذاشته شده‌اند، چون
              عددشان پیش‌پرداخت است نه قیمت خودرو.
            </p>
          </div>
        </Card>
      ) : (
        <Card title="خودروهای در دسترس">
          <Async
            query={result}
            shape="table"
            empty="با این مبلغ خودرویی پیدا نشد."
          >
            {(data) => (
              <>
                <div className="grid cols-4" style={{ marginBottom: 10 }}>
                  <Stat label="بودجه" value={toman(data.budget)} sub="تومان" />
                  <Stat
                    label="سقف بررسی"
                    value={toman(data.ceiling)}
                    sub={`با ${fa(data.tolerance_pct)}٪ تحمل`}
                  />
                  <Stat
                    label="دسته‌های در دسترس"
                    value={data.cohorts_matched.toLocaleString("en-US")}
                    sub="مدل، تیپ و سال ساخت"
                  />
                  <Stat
                    label="آگهی در این محدوده"
                    value={data.listings_matched.toLocaleString("en-US")}
                    sub="فعال و قیمت‌دار"
                  />
                </div>

                <Table
                  head={[
                    "خودرو", "سال", "چند درصد این دسته",
                    "زیر بودجه", "میانه قیمت", "ارزان‌ترین", "کارکرد میانه",
                  ]}
                >
                  {data.options.map((o) => (
                    <tr key={`${o.model_id}-${o.variant_id}-${o.year_jalali}`}>
                      <td>
                        <Link
                          to={`/analyse${qs({
                            model: o.model_id,
                            variant: o.variant_id ?? undefined,
                            year: o.year_jalali,
                          })}`}
                        >
                          <Fa>{o.name}</Fa>
                        </Link>
                        <div className="stat-sub">
                          <Fa>{[o.brand_name, o.variant_name].filter(Boolean).join(" · ")}</Fa>
                        </div>
                      </td>
                      <td className="num">{o.year_jalali}</td>
                      <td>
                        {/* The bar is the answer; the number beside it is the
                            evidence. A column of percentages alone makes the
                            reader do the comparison the ranking already did. */}
                        <div className="row" style={{ gap: 6 }}>
                          <span className="num">{pct(o.reach_pct, 0)}</span>
                          <span
                            className="bar"
                            style={{ width: `${Math.min(100, o.reach_pct)}%` }}
                          />
                        </div>
                        <div className="stat-sub">
                          {o.n.toLocaleString("en-US")} از{" "}
                          {o.cohort_size.toLocaleString("en-US")} آگهی
                        </div>
                      </td>
                      <td className="num">
                        {o.within_budget.toLocaleString("en-US")}
                      </td>
                      <td className="num">{toman(o.median_price)}</td>
                      <td className="num">{toman(o.cheapest)}</td>
                      <td className="num">{km(o.median_mileage)}</td>
                    </tr>
                  ))}
                </Table>

                {/* The distinction the two count columns carry, said once. */}
                <p className="empty-hint">
                  «چند درصد این دسته» یعنی بودجه شما چه سهمی از آگهی‌های آن خودرو
                  را پوشش می‌دهد — نه اینکه چقدر ارزان است. ستون «زیر بودجه»
                  آگهی‌هایی است که دقیقاً تا سقف مبلغ شما هستند؛ بقیه تا{" "}
                  {fa(data.tolerance_pct)}٪ بالاترند و برای همین نمایش داده شده‌اند.
                </p>
                <Provenance envelope={data} />
              </>
            )}
          </Async>
        </Card>
      )}
    </div>
  );
}
