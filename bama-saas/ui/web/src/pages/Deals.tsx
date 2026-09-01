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
import { AlertTriangle, Bell } from "lucide-react";
import { api } from "../api";
import type { Envelope } from "../api";
import { useAuth } from "../auth";
import { FilterPanel } from "../FilterPanel";
import { qs, useFilters } from "../filters";
import {
  Async, BamaLink, Card, ConfidenceDots, Fa, ListingActions, Pager, Provenance,
  Table, fa, pct, toman,
} from "../ui";
import { DealCard, conditionNote, type Deal } from "../components/DealCard";
import { ViewToggle, useListView } from "../components/ViewToggle";
import { Button } from "../components/ui/button";
import { Label } from "../components/ui/label";
import { Switch } from "../components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";
import {
  Popover, PopoverContent, PopoverTrigger,
} from "../components/ui/popover";

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

function NumberField({
  label, value, min, max, hint, onChange,
}: {
  label: string;
  value: number | string;
  min?: number;
  max?: number;
  hint?: string;
  onChange: (raw: string) => void;
}) {
  return (
    <div className="grid gap-1.5">
      <Label className="text-muted-foreground text-xs font-semibold">{label}</Label>
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="border-border bg-panel w-full rounded-md border px-2.5 py-1.5 text-sm"
      />
      {hint && <span className="text-muted-foreground text-[11px]">{hint}</span>}
    </div>
  );
}

/**
 * The rules that decide what is worth a Telegram message.
 *
 * Deliberately on this page rather than in a settings screen: the thresholds
 * only mean anything next to the board they filter. It is a popover on the
 * board's own header rather than a card below it — the adjacency is the point,
 * a permanent wall of number inputs under the results was not.
 *
 * Staff only. There is one notifier for the whole site, not one per reader, so
 * showing these controls to everyone offered every account a switch that turned
 * off the operator's alerts. The endpoint enforces this; the check here only
 * keeps the button from appearing and 403ing.
 */
function NotifierPanel() {
  const client = useQueryClient();
  const { user } = useAuth();
  const [form, setForm] = useState<NotifierSettings | null>(null);

  const settings = useQuery({
    queryKey: ["notifier-settings"],
    enabled: Boolean(user?.is_staff),
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
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm">
          <Bell className="size-4" />
          اعلان تلگرام
          <span className={`badge ${form.enabled ? "ok" : ""}`}>
            {form.enabled ? "روشن" : "خاموش"}
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(22rem,92vw)] space-y-3">
        <div className="flex items-center justify-between gap-3">
          <Label htmlFor="notify-on" className="text-sm font-bold">ارسال اعلان</Label>
          <Switch
            id="notify-on"
            checked={form.enabled}
            onCheckedChange={(enabled) => set({ enabled })}
          />
        </div>
        <p className="text-muted-foreground text-xs">
          آگهی‌هایی با دست‌کم {form.min_discount_pct}٪ تخفیف و {form.min_peers} آگهی
          مشابه — هر آگهی فقط یک بار.
        </p>

        <div className="grid grid-cols-2 gap-3">
          <NumberField
            label="کمترین تخفیف (٪)" min={1} max={99}
            value={form.min_discount_pct}
            onChange={(raw) => set({ min_discount_pct: Number(raw) })}
          />
          <NumberField
            label="کمترین آگهی مشابه" min={8}
            value={form.min_peers}
            hint="کمتر از ۸ پذیرفته نمی‌شود"
            onChange={(raw) => set({ min_peers: Number(raw) })}
          />
          <NumberField
            label="کمترین قیمت"
            value={form.price_min ?? ""}
            onChange={(raw) => set({ price_min: raw ? Number(raw) : null })}
          />
          <NumberField
            label="بیشترین قیمت"
            value={form.price_max ?? ""}
            onChange={(raw) => set({ price_max: raw ? Number(raw) : null })}
          />
        </div>

        <div className="grid gap-1.5">
          <Label className="text-muted-foreground text-xs font-semibold">
            شناسه گفت‌وگوی تلگرام
          </Label>
          <input
            dir="ltr"
            value={form.telegram_chat_id}
            onChange={(e) => set({ telegram_chat_id: e.target.value })}
            className="border-border bg-panel w-full rounded-md border px-2.5 py-1.5 font-mono text-sm"
          />
        </div>

        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => save.mutate(form)} disabled={save.isPending}>
            {save.isPending ? "در حال ذخیره…" : "ذخیره"}
          </Button>
          {save.isSuccess && !save.isPending && (
            <span className="badge ok">ذخیره شد</span>
          )}
          {save.isError && (
            <span className="badge warn">
              {(save.error as Error)?.message ?? "ذخیره نشد"}
            </span>
          )}
        </div>
        {/* Not a validation nicety: a median built from fewer peers is not a
            reliable basis for interrupting someone. */}
        <p className="text-muted-foreground text-[11px]">
          تعداد آگهی مشابه کمتر از ۸ پذیرفته نمی‌شود — میانه‌ای که از آگهی‌های کمتر
          ساخته شود، مبنای قابل اتکایی برای اعلان نیست.
        </p>
      </PopoverContent>
    </Popover>
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

// `all` is not "all ads": the API returns everything at or below the trusted
// ceiling whose discount the listing does not already explain, so the rows in
// `review` are excluded from it by design. The label used to say «همه آگهی‌ها»,
// which promised a superset the tab never returned — 26 listings were missing
// from it with nothing on screen to account for them.
//
// `ml` is a different question, not a re-sort of `top`. The other three rank on
// the gap to the cohort median — a key of (model, trim, year) that cannot see
// mileage, damage, city or who is selling. This one holds only listings the
// price model puts below its own predicted p10, ordered by that gap. The two
// disagree often, and where they disagree is the interesting part, so the
// board keeps both rather than replacing one with the other.
const TABS: { id: string; label: string }[] = [
  { id: "top", label: "پیشنهادهای برتر" },
  { id: "all", label: "بقیه معامله‌ها" },
  { id: "ml", label: "به تشخیص مدل" },
  { id: "review", label: "نیازمند بررسی" },
];

/** What the `ml` tab is, in one line, because the label cannot carry it. */
const ML_TAB_NOTE =
  "آگهی‌هایی که مدل یادگیرنده قیمتشان را پایین‌تر از پیش‌بینی خودش می‌داند. " +
  "برخلاف بقیه‌ی زبانه‌ها، این تخمین کارکرد، وضعیت بدنه، شهر و نوع فروشنده را " +
  "هم در نظر می‌گیرد. مبنای درصد تخفیف روی کارت‌ها همچنان میانه‌ی آگهی‌های مشابه است.";

export function Deals() {
  const filters = useFilters();
  const page = filters.getInt("page") ?? 1;
  const raw = filters.get("band");
  const band = TABS.some((t) => t.id === raw) ? raw! : "top";
  const view = useListView();

  const params = {
    band,
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
    brand: filters.get("brand"),
    model: filters.get("model"),
    price_min: filters.get("price_min"),
    price_max: filters.get("price_max"),
    year_min: filters.get("year_min"),
    year_max: filters.get("year_max"),
    mileage_min: filters.get("mileage_min"),
    mileage_max: filters.get("mileage_max"),
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
      <div className="row between">
        <Tabs
          value={band}
          onValueChange={(next) =>
            filters.set({ band: next === "top" ? null : next, page: null })
          }
        >
          <TabsList aria-label="دسته‌بندی آگهی‌ها">
            {TABS.map((t) => (
              <TabsTrigger key={t.id} value={t.id}>{t.label}</TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <NotifierPanel />
      </div>

      {/* The thresholds are computed, so the page quotes them rather than
          describing a filter whose value it does not know. */}
      <p className="muted">
        {band === "top" && (
          <>
            آگهی‌هایی که در <b>{fa(w?.window_days)} روز گذشته</b> ثبت یا
            به‌روزرسانی شده‌اند و دست‌کم{" "}
            {/* One decimal. The floor is a percentile of today's board, and
                printing it as "7.15%" offered the reader a precision that is
                an artefact of which listings happen to be live this hour. */}
            <b>{w ? pct(w.min_discount_pct, 1) : "—"}</b> زیر میانه قیمت
            آگهی‌های مشابه خود هستند. هر دو حد از همین آگهی‌های امروز محاسبه
            می‌شوند، نه از عددی ثابت. تازه‌ترین آگهی‌ها اول می‌آیند، چون قیمتشان
            به بازار امروز نزدیک‌تر است. خودروهایی که خودِ آگهی ارزانی‌شان را
            توضیح می‌دهد — رنگ‌شدگی یا تعویض قطعه — در زبانه «نیازمند بررسی»‌اند.
          </>
        )}
        {band === "all" && (
          <>
            بقیه آگهی‌های زیر میانه قیمت آگهی‌های مشابه، تا سقف{" "}
            <b>{w ? pct(w.ceiling_pct, 0) : "—"}</b>، در همان بازه{" "}
            <b>{fa(w?.window_days)} روزه</b> — یعنی همان‌هایی که به حد
            «پیشنهادهای برتر» نرسیده‌اند. گروه‌بندی بر پایه تازگی آگهی است و
            میزان تخفیف تنها ترتیب درون هر گروه را تعیین می‌کند.
          </>
        )}
        {band === "ml" && <>{ML_TAB_NOTE}</>}
        {band === "review" && (
          <>
            دو دسته آگهی که ارزانی‌شان دلیلی دارد بیرون از این محاسبه. نخست
            تخفیف بیش از <b>{w ? pct(w.ceiling_pct, 0) : "—"}</b>: آگهی‌های
            مشابه فقط بر پایه «مدل، تیپ و سال» انتخاب می‌شوند و چیزی درباره
            تصادف، پلاک منطقه آزاد یا پیش‌فروش نمی‌دانند. دوم خودروهایی که
            فروشنده خودش وضعیت بدنه‌شان را رنگ‌شده یا تعویض‌شده اعلام کرده —
            آن اختلاف قیمت، بهای همان رنگ است نه یک فرصت. چیزی پنهان نشده،
            اما پیش از تماس خودتان بررسی کنید.
          </>
        )}
      </p>

      <FilterPanel showSpecs={false} showConfidence showSearch={false} />

      <Card action={<ViewToggle />}>
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
                  band === "review" || band === "ml" ? (
                    // Neither of these is ranked by freshness — `review` by
                    // discount and `ml` by the model's residual — so banding
                    // them would draw groups the order does not follow, and
                    // the server sends no freshness rank for either, which
                    // would silently pile every row under "over two weeks".
                    <div className="card-grid">
                      {rows.map((d) => (
                        <DealCard key={d.code} deal={d}
                                  suspect={band === "review"} />
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
                            <div className="badge warn" style={{ marginTop: 4 }}
                                 title={conditionNote(d)}>
                              <AlertTriangle size={11} /> {d.body_status || "توضیحات وضعیت را بخوانید"}
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

    </div>
  );
}
