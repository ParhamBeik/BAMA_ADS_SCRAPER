/**
 * The deal board — the product's point.
 *
 * Every column here exists so the ranking can be checked rather than trusted.
 * The discount is measured against the cohort's own median, the peer count says
 * how many cars that median was built from, and the confidence tier says whether
 * the backend considers that enough. A 40% discount off three listings is not a
 * better deal than 12% off forty, and the board has to make that visible.
 *
 * Two corrections this screen carries, both from an audit of what it was
 * actually showing:
 *
 * **It defaults to the ≤30% band.** An audit of the top 200 rows found 74% were
 * installment ads advertising a down payment rather than a price. Those are now
 * excluded upstream (`listing_kind.exclude_unclear_price`), but the deeper
 * problem survives the filter: the cohort key is `(model, variant, year)` and
 * knows nothing about accident damage, free-zone plates or pre-sales, so above
 * ~30% the gap is essentially always an attribute the model cannot see rather
 * than a bargain. Those rows are still reachable — under a tab that says what
 * they are, instead of on a page that calls them the best deals available.
 *
 * **It paginates.** The board holds ~8,600 rows and this screen used to request
 * a hard-coded top 50 with no way forward, so every genuine 5–20% deal in the
 * cache was unreachable.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, LayoutGrid, List } from "lucide-react";
import { api } from "../api/client";
import type { Envelope } from "../api/client";
import { qs, useFilters } from "../filters";
import {
  Async, Card, ConfidenceDots, Fa, Provenance, Table, Thumb, km, pct, toman,
} from "../ui";
import { ListingActions } from "../engagement";

/** Above this, the gap is an unmodelled attribute far more often than a deal. */
const TRUSTED_MAX_DISCOUNT = 30;
/** score is rounded to 1 decimal (deal_score.py); the smallest step above it. */
const SCORE_STEP = 0.1;
const PAGE_SIZE = 24;

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
  year: number | null;
  mileage: number | null;
  city_name: string;
  primary_image_url: string;
  condition_flagged: boolean;
}

interface DealBoard extends Envelope {
  count: number;
  limit: number;
  offset: number;
  results: Deal[];
}

interface Brand {
  slug: string;
  name_fa: string;
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
  const set = (patch: Partial<NotifierSettings>) =>
    setForm({ ...form, ...patch });

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
          آگهی‌هایی با حداقل {form.min_discount_pct}٪ تخفیف و {form.min_peers}{" "}
          هم‌گروه — هر آگهی فقط یک‌بار.
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
              حداقل تخفیف (٪)
              <input
                type="number" min={1} max={99} value={form.min_discount_pct}
                onChange={(e) => set({ min_discount_pct: Number(e.target.value) })}
              />
            </label>
            <label>
              حداقل هم‌گروه
              <input
                type="number" min={8} value={form.min_peers}
                onChange={(e) => set({ min_peers: Number(e.target.value) })}
              />
            </label>
            <label>
              حداقل قیمت
              <input
                type="number" value={form.price_min ?? ""}
                onChange={(e) =>
                  set({ price_min: e.target.value ? Number(e.target.value) : null })
                }
              />
            </label>
            <label>
              حداکثر قیمت
              <input
                type="number" value={form.price_max ?? ""}
                onChange={(e) =>
                  set({ price_max: e.target.value ? Number(e.target.value) : null })
                }
              />
            </label>
            <label>
              شناسهٔ چت تلگرام
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
            حداقل هم‌گروه کمتر از ۸ پذیرفته نمی‌شود — میانه‌ای که از آگهی کمتری
            ساخته شود، مبنای قابل اتکایی برای اعلان نیست.
          </p>
        </div>
      )}
    </Card>
  );
}

/** "0 روز در بازار" is technically right and reads like a bug. */
function ageLabel(days: number | null): string {
  if (days == null) return "—";
  if (days === 0) return "امروز ثبت شده";
  return `${days} روز در بازار`;
}

function DealCard({ deal }: { deal: Deal }) {
  const suspect = (deal.discount_pct ?? 0) > TRUSTED_MAX_DISCOUNT;
  return (
    <Link to={`/listing/${deal.code}`} className="listing-card">
      <Thumb src={deal.primary_image_url}>
        <span className={`ribbon${suspect ? " suspect" : ""}`}>
          {pct(deal.discount_pct, 0)}
        </span>
        {deal.condition_flagged && (
          <span className="card-badges">
            <span className="badge warn" title="توضیحات آگهی به تصادف، پلاک منطقهٔ آزاد یا وضعیت بدنه اشاره دارد">
              <AlertTriangle size={11} /> وضعیت
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
          <span>{deal.peer_count ?? "—"} هم‌گروه</span>
          <span>·</span>
          <span>{deal.year ?? "—"}</span>
          <span>·</span>
          <span>{km(deal.mileage)}</span>
        </div>
        <div className="row">
          <Fa>{deal.city_name || "—"}</Fa>
          <span>·</span>
          <span>{ageLabel(deal.age_days)}</span>
        </div>
      </div>
    </Link>
  );
}

export function Deals() {
  const filters = useFilters();
  const page = filters.getInt("page") ?? 1;
  const band = filters.get("band") === "review" ? "review" : "trusted";
  const view = filters.get("view") === "table" ? "table" : "cards";
  const brand = filters.get("brand");
  const priceMin = filters.get("price_min");
  const priceMax = filters.get("price_max");
  const confidence = filters.get("confidence");

  const brands = useQuery({
    queryKey: ["brands"],
    queryFn: ({ signal }) =>
      api.get<{ results?: Brand[] } | Brand[]>("/api/brands/", signal),
  });
  const brandList: Brand[] = Array.isArray(brands.data)
    ? brands.data
    : (brands.data?.results ?? []);

  const params = {
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
    brand,
    price_min: priceMin,
    price_max: priceMax,
    confidence,
    // The band is the whole point of the tab: one query, two windows onto it.
    // Backend min_score/max_score are both inclusive, so the boundary value
    // itself must land in exactly one window — trusted claims it (<=), review
    // starts one score step above (>), matching the ribbon's ">" suspect check.
    ...(band === "review"
      ? { min_score: TRUSTED_MAX_DISCOUNT + SCORE_STEP }
      : { max_score: TRUSTED_MAX_DISCOUNT }),
  };

  const deals = useQuery({
    queryKey: ["deal-scores", params],
    queryFn: ({ signal }) =>
      api.get<DealBoard>(`/api/analytics/deal-scores/${qs(params)}`, signal),
  });

  const hasFilter = Boolean(brand || priceMin || priceMax || confidence);
  const clear = () =>
    filters.set({
      brand: null, price_min: null, price_max: null, confidence: null, page: null,
    });

  return (
    <div className="stack" dir="rtl">
      <div className="segmented" role="group" aria-label="محدودهٔ تخفیف">
        <button
          className={band === "trusted" ? "on" : ""}
          onClick={() => filters.set({ band: null, page: null })}
        >
          پیشنهادهای قابل اتکا
        </button>
        <button
          className={band === "review" ? "on" : ""}
          onClick={() => filters.set({ band: "review", page: null })}
        >
          نیاز به بررسی (بالای {TRUSTED_MAX_DISCOUNT}٪)
        </button>
      </div>

      <p className="muted">
        {band === "trusted" ? (
          <>
            آگهی‌هایی که زیر میانهٔ هم‌گروه خود قیمت خورده‌اند. تخفیف نسبت به
            میانهٔ هم‌گروه محاسبه می‌شود؛ «هم‌گروه» و نقطه‌های اعتماد نشان
            می‌دهند این میانه از چند آگهی ساخته شده است.
          </>
        ) : (
          <>
            تخفیف بالای {TRUSTED_MAX_DISCOUNT}٪ تقریباً همیشه دلیلی دارد که
            مدل آن را نمی‌بیند: هم‌گروه فقط از «مدل، تیپ و سال» ساخته می‌شود و
            از تصادف، پلاک منطقهٔ آزاد یا پیش‌فروش خبر ندارد. این‌ها پنهان
            نشده‌اند، اما پیش از تماس باید خودتان بررسی‌شان کنید.
          </>
        )}
      </p>

      <div className="filters">
        <select
          value={brand ?? ""}
          onChange={(e) => filters.set({ brand: e.target.value || null, page: null })}
          aria-label="برند"
        >
          <option value="">همهٔ برندها</option>
          {brandList.map((b) => (
            <option key={b.slug} value={b.slug}>{b.name_fa}</option>
          ))}
        </select>
        <select
          value={confidence ?? ""}
          onChange={(e) => filters.set({ confidence: e.target.value || null, page: null })}
          aria-label="اعتماد"
        >
          <option value="">هر اعتمادی</option>
          <option value="high">فقط اعتماد بالا</option>
          <option value="medium">اعتماد متوسط</option>
          <option value="low">اعتماد کم</option>
        </select>
        <input
          key={`price_min-${priceMin}`}
          type="number"
          placeholder="حداقل قیمت (تومان)"
          defaultValue={priceMin ?? ""}
          onBlur={(e) => filters.set({ price_min: e.target.value || null, page: null })}
        />
        <input
          key={`price_max-${priceMax}`}
          type="number"
          placeholder="حداکثر قیمت (تومان)"
          defaultValue={priceMax ?? ""}
          onBlur={(e) => filters.set({ price_max: e.target.value || null, page: null })}
        />
        {hasFilter && <button onClick={clear}>پاک کردن فیلترها</button>}
        <div className="segmented" style={{ marginInlineStart: "auto" }}>
          <button
            className={view === "cards" ? "on" : ""}
            onClick={() => filters.set({ view: null })}
            aria-label="نمای کارت"
          >
            <LayoutGrid size={14} />
          </button>
          <button
            className={view === "table" ? "on" : ""}
            onClick={() => filters.set({ view: "table" })}
            aria-label="نمای جدول"
          >
            <List size={14} />
          </button>
        </div>
      </div>

      <Card>
        <Async query={deals} empty="هنوز امتیازی محاسبه نشده." shape="cards">
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
                      ? "شاید این صفحه دیگر وجود ندارد."
                      : "فیلترها را ساده‌تر کنید یا برند دیگری را امتحان کنید."}
                  </p>
                  {page > 1 && (
                    <button onClick={() => filters.set({ page: null })}>
                      بازگشت به صفحهٔ اول
                    </button>
                  )}
                </div>
              );
            }
            const total = board.count ?? rows.length;
            const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
            return (
              <>
                {view === "cards" ? (
                  <div className="card-grid">
                    {rows.map((d) => (
                      <DealCard key={d.code} deal={d} />
                    ))}
                  </div>
                ) : (
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
                          {d.condition_flagged && (
                            <div className="badge warn" style={{ marginTop: 4 }}>
                              <AlertTriangle size={11} /> وضعیت خودرو را بخوانید
                            </div>
                          )}
                        </td>
                        <td className="num up">{pct(d.discount_pct)}</td>
                        <td className="num">{toman(d.price)}</td>
                        <td className="num">{toman(d.peer_median)}</td>
                        <td className="num">{d.peer_count ?? "—"}</td>
                        <td className="num"><ConfidenceDots tier={d.confidence} /></td>
                        <td className="num">
                          {d.age_days != null ? `${d.age_days}d` : "—"}
                        </td>
                        <td className="num">
                          <ListingActions code={d.code} />
                        </td>
                      </tr>
                    ))}
                  </Table>
                )}

                <div className="filters" style={{ marginTop: 14, marginBottom: 0 }}>
                  <button
                    disabled={page <= 1}
                    onClick={() => filters.set({ page: page - 1 })}
                  >
                    قبلی
                  </button>
                  <span className="stat-sub">
                    صفحهٔ {page.toLocaleString("en-US")} از{" "}
                    {lastPage.toLocaleString("en-US")} ·{" "}
                    {total.toLocaleString("en-US")} آگهی
                  </span>
                  <button
                    disabled={page >= lastPage}
                    onClick={() => filters.set({ page: page + 1 })}
                  >
                    بعدی
                  </button>
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
