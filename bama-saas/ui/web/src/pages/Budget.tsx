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
import { Async, Card, Fa, NumberInput, Provenance, HowWeKnow } from "../ui";
import { carName, fa, km, num, parseBudget, toman } from "../format";
import { Button } from "../components/ui/button";

interface Option {
  model_id: number;
  variant_id: number | null;
  year_jalali: number;
  /** Every model year folded into this row; `year_jalali` is the newest the
   *  budget reaches most of. */
  year_min: number;
  year_max: number;
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
  ["300 میلیون", 300_000_000],
  ["500 میلیون", 500_000_000],
  ["1 میلیارد", BILLION],
  ["2 میلیارد", 2 * BILLION],
  ["5 میلیارد", 5 * BILLION],
];

const TOLERANCES = [0, 5, 10, 20];
const PAGE = 40;

export function Budget() {
  const filters = useFilters();
  const budget = filters.getInt("budget");
  const tolerance = filters.getInt("tolerance") ?? 10;
  const [draft, setDraft] = useState(budget ? String(budget) : "");
  // Grows by a page per "more"; a new budget or tolerance starts over.
  const [shown, setShown] = useState({ key: "", limit: PAGE });
  const searchKey = `${budget}-${tolerance}`;
  const limit = shown.key === searchKey ? shown.limit : PAGE;

  const result = useQuery({
    queryKey: ["affordable", budget, tolerance, limit],
    enabled: Boolean(budget),
    // The shorter list stays on screen while the longer one loads — for the
    // same search only, never a different budget's answer.
    placeholderData: (previous: Affordable | undefined, prior) =>
      prior?.queryKey[1] === budget && prior?.queryKey[2] === tolerance ? previous : undefined,
    queryFn: ({ signal }) =>
      api.get<Affordable>(
        `/api/analytics/affordable/${qs({ budget, tolerance, limit })}`, signal),
  });

  const commit = (value: string) => {
    filters.set({ budget: parseBudget(value) });
  };

  return (
    <div className="stack">
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
          <NumberInput
            className="border-border bg-panel min-w-0 flex-1 rounded-md border px-3 py-2 text-sm sm:max-w-64"
            aria-label="بودجه به تومان"
            placeholder="مثلاً 1000000000"
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
              {t === 0 ? "دقیقاً همین مبلغ" : `تا ${fa(t)}% بیشتر`}
            </button>
          ))}
        </div>
      </Card>

      {!budget ? (
        <Card>
          <div className="state">
            <strong>مبلغی وارد کنید.</strong>
            <HowWeKnow><p>
              نتیجه بر پایه آگهی‌های فعال و قیمت‌دار است — همان آگهی‌هایی که در
              بخش جست‌وجو می‌بینید. آگهی‌های اقساطی کنار گذاشته شده‌اند، چون
              عددشان پیش‌پرداخت است نه قیمت خودرو.
            </p></HowWeKnow>
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
                <p className="stat-sub" style={{ marginBottom: 10 }}>
                  {num(data.cohorts_matched)} گزینه · {num(data.listings_matched)} آگهی تا{" "}
                  <bdi>{toman(data.ceiling)}</bdi> تومان
                </p>

                {/* One row per car, not a seven-column table: on a phone the
                    table was 533 px inside a 309 px box. */}
                <ul className="budget-list">
                  {data.options.map((o) => (
                    <li key={`${o.model_id}-${o.variant_id}-${o.year_jalali}`}>
                      <Link
                        to={`/price${qs({
                          model: o.model_id,
                          variant: o.variant_id ?? undefined,
                          year: o.year_jalali,
                        })}`}
                        className="budget-row"
                      >
                        <span className="budget-name">
                          <Fa>{carName(o.brand_name, o.name, o.variant_name)}</Fa>
                          <span className="muted">
                            {" · "}
                            {o.year_min === o.year_max ? o.year_min : `${o.year_min} تا ${o.year_max}`}
                          </span>
                        </span>
                        <span className="budget-price">
                          {o.year_min !== o.year_max && <>{o.year_jalali}: </>}
                          میانه <bdi>{toman(o.median_price)}</bdi>
                        </span>
                        <span className="stat-sub">
                          {num(o.within_budget)} آگهی زیر بودجه
                          {o.median_mileage != null && <> · کارکرد میانه {km(o.median_mileage)}</>}
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>

                {data.options.length < data.cohorts_matched && (
                  <Button
                    variant="outline"
                    className="mt-3 w-full"
                    disabled={result.isFetching}
                    onClick={() => setShown({ key: searchKey, limit: limit + PAGE })}
                  >
                    {result.isFetching
                      ? "در حال دریافت…"
                      : `${num(data.cohorts_matched - data.options.length)} گزینه‌ی دیگر`}
                  </Button>
                )}

                <Provenance envelope={data} />
              </>
            )}
          </Async>
        </Card>
      )}
    </div>
  );
}
