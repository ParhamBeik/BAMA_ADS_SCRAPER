/**
 * The deal board — the product's point.
 *
 * Every column here exists so the ranking can be checked rather than trusted.
 * The discount is measured against the peer group's own median, the peer count
 * says how many cars that median was built from, and the confidence dots say
 * whether the backend considers that enough. A 40% discount off three listings
 * is not a better deal than 12% off forty, and the board has to make that
 * visible.
 *
 * **Freshness ranks before size of discount.** A three-week-old asking price is
 * a worse guide to what a car costs today than a fresh one, however large the
 * gap looks — so the board groups by how recently the ad was posted or bumped,
 * and the discount only decides order *within* a group. Without the group
 * headings this reads as a broken sort, which is why they are not decoration.
 *
 * **Both thresholds are measured, not chosen.** How far back the board looks
 * and how good a deal has to be to appear are computed per rebuild from the
 * batch actually on the board (`pricing.deal_window`), so a quiet day widens
 * the window instead of showing three cars.
 *
 * **Above 25% goes to review, not to the front page.** The peer group is
 * (model, trim, year) and knows nothing about accident damage, free-zone plates
 * or pre-sales, so past that point the gap is an unmodelled attribute far more
 * often than a bargain. Nothing is hidden — it is moved to a tab that says what
 * it is.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, LayoutGrid, List } from "lucide-react";
import { api } from "../api";
import type { Envelope } from "../api";
import { FilterPanel } from "../FilterPanel";
import { qs, useFilters } from "../filters";
import {
  Async, BamaLink, Card, ConfidenceDots, Fa, ListingActions, Pager, Provenance,
  Table, Thumb, km, pct, toman,
} from "../ui";

const PAGE_SIZE = 24;

/**
 * Names for the freshness bands. The *index* comes from the API (`freshness`),
 * which is the same value SQL ordered by — this file only labels it.
 *
 * Deliberately not recomputed here from `days_listed`: that number is floored to
 * whole days, so every band edge lands on the wrong side (an ad aged 3.5 days
 * floors to 3 and would read as "۱ تا ۳ روز" while the backend sorted it into
 * "۴ تا ۷ روز"), and a row placed in an earlier band than its neighbours drew a
 * second copy of that band's heading further down the grid.
 */
const BAND_LABEL = [
  "امروز",
  "۱ تا ۳ روز پیش",
  "۴ تا ۷ روز پیش",
  "۱ تا ۲ هفته پیش",
  "بیش از دو هفته پیش",
];

const LAST_BAND = BAND_LABEL.length - 1;

function bandOf(deal: Deal): number {
  const band = deal.freshness;
  if (band == null || band < 0 || band > LAST_BAND) return LAST_BAND;
  return band;
}

interface NotifierSettings {
  enabled: boolean;
  min_discount_pct: number;
  min_peers: number;
  price_min: number | null;
  price_max: number | null;
  telegram_chat_id: string;
}

interface Deal {
  code: string;
  title: string;
  discount_pct: number | null;
  price: number | null;
  peer_median: number | null;
  peer_count: number | null;
  confidence: string | null;
  age_days: number | null;
  days_listed: number | null;
  /** Band index the API ordered by; see BAND_LABEL. */
  freshness: number | null;
  year: number | null;
  mileage: number | null;
  city_name: string;
  image_url: string;
  bama_url: string;
  condition_flagged: boolean;
}

interface DealWindow {
  window_days: number;
  min_discount_pct: number;
  ceiling_pct: number;
  candidates: number;
  scored: number;
}

interface DealBoard extends Envelope {
  count: number;
  limit: number;
  offset: number;
  band: string;
  window: DealWindow;
  results: Deal[];
}

/**
 * The rules that decide what is worth a Telegram message.
 *
 * Deliberately on this page rather than in a settings screen: the thresholds
 * only mean anything next to the board they filter.
 */
function NotifierPanel() {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<NotifierSettings | null>(null);

  const settings = useQuery({
    queryKey: ["notifier-settings"],
    queryFn: ({ signal }) =>
      api.get<NotifierSettings>("/api/notifier-settings/", signal),
  });

  useEffect(() => {
    if (settings.data && !form) setForm(settings.data);
  }, [settings.data, form]);

  const save = useMutation({
    mutationFn: (body: Partial<NotifierSettings>) =>
      api.patch<NotifierSettings>("/api/notifier-settings/", body),
    onSuccess: (data) => {
      setForm(data);
      client.invalidateQueries({ queryKey: ["notifier-settings"] });
    },
  });

  if (!form) return null;
  const set = (patch: Partial<NotifierSettings>) => setForm({ ...form, ...patch });

  return (
    <Card>
      <div className="row between">
        <strong>
          اعلان تلگرام{" "}
          <span className={`badge ${form.enabled ? "ok" : ""}`}>
            {form.enabled ? "روشن" : "خاموش"}
          </span>
        </strong>
        <button className="ghost" onClick={() => setOpen(!open)}>
          {open ? "بستن" : "تنظیمات"}
        </button>
      </div>

      {!open ? (
        <p className="muted">
          آگهی‌هایی با دست‌کم {form.min_discount_pct}٪ تخفیف و {form.min_peers} آگهی
          مشابه — هر آگهی فقط یک بار.
        </p>
      ) : (
        <div className="stack">
          <label className="row">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => set({ enabled: e.target.checked })}
            />
            <span>ارسال اعلان</span>
          </label>
          <div className="row wrap">
            <label>
              کمترین تخفیف (٪)
              <input
                type="number" min={1} max={99} value={form.min_discount_pct}
                onChange={(e) => set({ min_discount_pct: Number(e.target.value) })}
              />
            </label>
            <label>
              کمترین تعداد آگهی مشابه
              <input
                type="number" min={8} value={form.min_peers}
                onChange={(e) => set({ min_peers: Number(e.target.value) })}
              />
            </label>
            <label>
              کمترین قیمت
              <input
                type="number" value={form.price_min ?? ""}
                onChange={(e) =>
                  set({ price_min: e.target.value ? Number(e.target.value) : null })
                }
              />
            </label>
            <label>
              بیشترین قیمت
              <input
                type="number" value={form.price_max ?? ""}
                onChange={(e) =>
                  set({ price_max: e.target.value ? Number(e.target.value) : null })
                }
              />
            </label>
            <label>
              شناسه گفت‌وگوی تلگرام
              <input
                value={form.telegram_chat_id}
                onChange={(e) => set({ telegram_chat_id: e.target.value })}
              />
            </label>
          </div>
          <div className="row">
            <button onClick={() => save.mutate(form)} disabled={save.isPending}>
              {save.isPending ? "در حال ذخیره…" : "ذخیره"}
            </button>
            {save.isSuccess && !save.isPending && (
              <span className="badge ok">ذخیره شد</span>
            )}
            {save.isError && (
              <span className="badge warn">
                {(save.error as Error)?.message ?? "ذخیره نشد"}
              </span>
            )}
          </div>
          <p className="muted">
            تعداد آگهی مشابه کمتر از ۸ پذیرفته نمی‌شود — میانه‌ای که از آگهی‌های
            کمتر ساخته شود، مبنای قابل اتکایی برای اعلان نیست.
          </p>
        </div>
      )}
    </Card>
  );
}

/** "۰ روز در بازار" is technically right and reads like a bug. */
function ageLabel(days: number | null): string {
  if (days == null) return "—";
  if (days <= 0) return "امروز ثبت شده";
  return `${days} روز در بازار`;
}

function DealCard({ deal, suspect }: { deal: Deal; suspect: boolean }) {
  return (
    <Link to={`/listing/${deal.code}`} className="listing-card">
      <Thumb src={deal.image_url}>
        <span className={`ribbon${suspect ? " suspect" : ""}`}>
          {pct(deal.discount_pct, 0)}
        </span>
        {deal.condition_flagged && (
          <span className="card-badges">
            <span
              className="badge warn"
              title="توضیحات آگهی به تصادف، پلاک منطقه آزاد یا وضعیت بدنه اشاره کرده است"
            >
              <AlertTriangle size={11} /> وضعیت بدنه
            </span>
          </span>
        )}
      </Thumb>
      <div className="listing-meta">
        <strong>
          <Fa>{deal.title || deal.code}</Fa>
        </strong>
        <div className="row">
          <span className="deal-price">{toman(deal.price)}</span>
          <span className="deal-median">{toman(deal.peer_median)}</span>
        </div>
        <div className="row">
          <ConfidenceDots tier={deal.confidence} />
          <span>{deal.peer_count ?? "—"} آگهی مشابه</span>
          <span>·</span>
          <span>{deal.year ?? "—"}</span>
          <span>·</span>
          <span>{km(deal.mileage)}</span>
        </div>
        <div className="row">
          <Fa>{deal.city_name || "—"}</Fa>
          <span>·</span>
          <span>{ageLabel(deal.days_listed)}</span>
        </div>
        <div className="row">
          <BamaLink href={deal.bama_url} className="ghost" />
        </div>
      </div>
    </Link>
  );
}

/** The cards, with a heading wherever the freshness band changes. */
function BandedGrid({ rows, ceiling }: { rows: Deal[]; ceiling: number }) {
  const out: React.ReactNode[] = [];
  let current = -1;
  let bucket: Deal[] = [];

  const flush = () => {
    if (!bucket.length) return;
    const band = current;
    const items = bucket;
    out.push(
      <div key={`h${band}-${items[0].code}`} className="band-heading">
        {BAND_LABEL[band]}
        <span className="badge">{items.length}</span>
      </div>,
      <div key={`g${band}-${items[0].code}`} className="card-grid">
        {items.map((d) => (
          <DealCard key={d.code} deal={d} suspect={(d.discount_pct ?? 0) > ceiling} />
        ))}
      </div>,
    );
    bucket = [];
  };

  for (const deal of rows) {
    const band = bandOf(deal);
    if (band !== current) {
      flush();
      current = band;
    }
    bucket.push(deal);
  }
  flush();
  return <>{out}</>;
}

const TABS: { id: string; label: string }[] = [
  { id: "top", label: "پیشنهادهای برتر" },
  { id: "all", label: "همه آگهی‌ها" },
  { id: "review", label: "نیازمند بررسی" },
];

export function Deals() {
  const filters = useFilters();
  const page = filters.getInt("page") ?? 1;
  const raw = filters.get("band");
  const band = TABS.some((t) => t.id === raw) ? raw! : "top";
  const view = filters.get("view") === "table" ? "table" : "cards";

  const params = {
    band,
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
    brand: filters.get("brand"),
    model: filters.get("model"),
    price_min: filters.get("price_min"),
    price_max: filters.get("price_max"),
    confidence: filters.get("confidence"),
  };

  const deals = useQuery({
    queryKey: ["deal-scores", params],
    queryFn: ({ signal }) =>
      api.get<DealBoard>(`/api/analytics/deal-scores/${qs(params)}`, signal),
  });

  const w = deals.data?.window;

  return (
    <div className="stack">
      <div className="segmented" role="group" aria-label="دسته‌بندی آگهی‌ها">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={band === t.id ? "on" : ""}
            aria-pressed={band === t.id}
            onClick={() => filters.set({ band: t.id === "top" ? null : t.id, page: null })}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* The thresholds are computed, so the page quotes them rather than
          describing a filter whose value it does not know. */}
      <p className="muted">
        {band === "top" && (
          <>
            آگهی‌هایی که در <b>{w?.window_days ?? "—"} روز گذشته</b> ثبت یا
            به‌روزرسانی شده‌اند و دست‌کم <b>{w?.min_discount_pct ?? "—"}٪</b> زیر
            میانه قیمت آگهی‌های مشابه خود هستند. هر دو حد از همین آگهی‌های امروز
            محاسبه می‌شوند، نه از عددی ثابت. تازه‌ترین آگهی‌ها اول می‌آیند، چون
            قیمتشان به بازار امروز نزدیک‌تر است.
          </>
        )}
        {band === "all" && (
          <>
            همه آگهی‌های زیر میانه قیمت آگهی‌های مشابه، تا سقف{" "}
            <b>{w?.ceiling_pct ?? "—"}٪</b>. گروه‌بندی بر پایه تازگی آگهی است و
            میزان تخفیف تنها ترتیب درون هر گروه را تعیین می‌کند.
          </>
        )}
        {band === "review" && (
          <>
            تخفیف بیش از <b>{w?.ceiling_pct ?? "—"}٪</b> تقریباً همیشه دلیلی دارد
            که این محاسبه نمی‌بیند: آگهی‌های مشابه فقط بر پایه «مدل، تیپ و سال»
            انتخاب می‌شوند و چیزی درباره تصادف، پلاک منطقه آزاد یا پیش‌فروش
            نمی‌دانند. چیزی پنهان نشده، اما پیش از تماس خودتان بررسی کنید.
          </>
        )}
      </p>

      <FilterPanel showSpecs={false} showConfidence showSearch={false} />

      <Card
        action={
          <div className="segmented">
            <button
              className={view === "cards" ? "on" : ""}
              onClick={() => filters.set({ view: null })}
              aria-label="نمایش کارتی"
            >
              <LayoutGrid size={14} />
            </button>
            <button
              className={view === "table" ? "on" : ""}
              onClick={() => filters.set({ view: "table" })}
              aria-label="نمایش جدولی"
            >
              <List size={14} />
            </button>
          </div>
        }
      >
        <Async query={deals} empty="هنوز امتیازی محاسبه نشده است." shape="cards">
          {(board) => {
            const rows = board.results ?? [];
            if (!rows.length) {
              // page > 1 with zero rows usually means the page fell out of
              // range (fewer results now than when this URL was built), not
              // that there are no matches at all — leave a way back to page 1
              // instead of a dead end.
              return (
                <div className="state">
                  <strong>آگهی‌ای با این فیلترها پیدا نشد.</strong>
                  <p className="empty-hint">
                    {page > 1
                      ? "ممکن است این صفحه دیگر وجود نداشته باشد."
                      : "فیلترها را ساده‌تر کنید یا برند دیگری را امتحان کنید."}
                  </p>
                  {page > 1 && (
                    <button onClick={() => filters.set({ page: null })}>
                      بازگشت به صفحه اول
                    </button>
                  )}
                </div>
              );
            }
            const total = board.count ?? rows.length;
            const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
            const ceiling = board.window?.ceiling_pct ?? 25;
            return (
              <>
                {view === "cards" ? (
                  band === "review" ? (
                    // The review tab is ranked by discount, not by freshness,
                    // so banding it would draw groups the order does not follow.
                    <div className="card-grid">
                      {rows.map((d) => (
                        <DealCard key={d.code} deal={d} suspect />
                      ))}
                    </div>
                  ) : (
                    <BandedGrid rows={rows} ceiling={ceiling} />
                  )
                ) : (
                  <Table
                    head={[
                      "آگهی", "تخفیف", "قیمت", "میانه مشابه‌ها",
                      "تعداد مشابه", "اعتبار", "در بازار", "",
                    ]}
                  >
                    {rows.map((d) => (
                      <tr key={d.code}>
                        <td>
                          <Link to={`/listing/${d.code}`}>
                            <Fa>{d.title || d.code}</Fa>
                          </Link>
                          {d.condition_flagged && (
                            <div className="badge warn" style={{ marginTop: 4 }}>
                              <AlertTriangle size={11} /> توضیحات وضعیت را بخوانید
                            </div>
                          )}
                        </td>
                        <td className="num up">{pct(d.discount_pct)}</td>
                        <td className="num">{toman(d.price)}</td>
                        <td className="num">{toman(d.peer_median)}</td>
                        <td className="num">{d.peer_count ?? "—"}</td>
                        <td className="num"><ConfidenceDots tier={d.confidence} /></td>
                        <td className="num">
                          {d.days_listed != null ? `${d.days_listed} روز` : "—"}
                        </td>
                        <td className="num">
                          <div className="row">
                            <ListingActions code={d.code} />
                            <BamaLink href={d.bama_url} className="ghost">باما</BamaLink>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </Table>
                )}

                <div style={{ marginTop: 14 }}>
                  <Pager
                    page={page}
                    lastPage={lastPage}
                    total={total}
                    onChange={(next) => filters.set({ page: next })}
                  />
                </div>
                <Provenance envelope={board} />
              </>
            );
          }}
        </Async>
      </Card>

      <NotifierPanel />
    </div>
  );
}
