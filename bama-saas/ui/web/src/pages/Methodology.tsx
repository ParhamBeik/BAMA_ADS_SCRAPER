/**
 * What produced the numbers, and how well it did.
 *
 * Every research answer on this site ships a `methodology_version` and, until
 * now, that badge pointed at nothing: a version number rendered on every screen
 * with no document behind it. This is the document, and it is generated from
 * the registry rather than written by hand, so it cannot drift from what is
 * actually running.
 *
 * The design decision worth stating: **models that were not promoted are shown
 * too, with the reason.** A page that lists only the winners is marketing. The
 * interesting row is the one that says a challenger lost to the peer median and
 * is sitting in shadow — that is what makes the rest of the page believable,
 * and it is the whole argument for having a promotion gate rather than shipping
 * whatever was trained last.
 *
 * Prose is composed here, not in the API. The endpoint returns machine keys and
 * measured facts; the Persian sentences that explain what a Brier score is or
 * why an 80% interval matters belong on the screen that draws them.
 */
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, Info, XCircle } from "lucide-react";
import { api } from "@/api";
import { Async, Card, Fa, Provenance, fa, pct } from "@/ui";
import type { Envelope } from "@/api";

interface Promotion {
  promote: boolean;
  reason: string;
  challenger: number | null;
  incumbent: number | null;
  baseline: number | null;
  lower_is_better?: boolean;
  margin?: number;
  vetoed?: boolean;
}

interface ReliabilityBin {
  bin_lower: number;
  bin_upper: number;
  n: number;
  mean_predicted: number;
  observed: number;
}

interface ModelCard {
  name: string;
  label: string;
  version: number;
  status: "shadow" | "active" | "retired";
  algorithm: string;
  trained_at: string;
  trained_through: string | null;
  training_rows: number;
  notes: string;
  features: string[];
  metrics: Record<string, unknown> & { promotion?: Promotion };
}

interface ModelsResponse extends Envelope {
  available?: boolean;
  reason?: string;
  models: ModelCard[];
  active?: Record<string, number>;
  scored_ads?: number;
  /** How many cards are on the page, and how many versions exist behind them.
      Both are rendered: this is the one page whose argument is that the numbers
      can be checked, so a page that silently showed five of sixty-three trained
      versions without saying so would be undercutting its own claim. */
  shown?: number;
  trained_total?: number;
  history_per_model?: number;
}

/**
 * The model's name in the language the rest of the site is in.
 *
 * The API does return a `label`, but it is `MLModel.Name`'s English choice text
 * — "Quantile price model (p10/p50/p90)" — which is the right thing for the
 * Django admin and the wrong thing here. Prose is composed in the UI: the API
 * hands over the machine key and this maps it.
 */
const TITLE: Record<string, string> = {
  price: "برآورد قیمت با بازه",
  sell_fast: "احتمال برداشته‌شدن آگهی",
  anomaly: "تشخیص آگهی غیرعادی",
  model_text: "تطبیق متن آگهی با کاتالوگ",
  value_tier: "لایه‌های ارزشی هر تیپ",
};

/**
 * What each model is for, in the language of the question it answers rather
 * than of the algorithm that answers it. A reader who wants "LGBMRegressor"
 * has it on the card already.
 */
const PURPOSE: Record<string, string> = {
  price:
    "قیمت این خودرو چقدر باید باشد — و مهم‌تر، بازه‌ای که قیمت واقعی با احتمال بالا داخل آن است. " +
    "برخلاف میانه‌ی آگهی‌های مشابه، این مدل کارکرد، وضعیت بدنه، شهر، نوع فروشنده و چند ستون دیگر را هم می‌بیند.",
  sell_fast:
    "احتمال اینکه این آگهی ظرف بازه‌ی تعیین‌شده از باما برداشته شود. " +
    "«برداشته شود»، نه «فروخته شود»: باما دلیل حذف آگهی را منتشر نمی‌کند.",
  anomaly:
    "جدا کردن دو چیزی که یک آستانه‌ی ساده نمی‌تواند: خودرویی که واقعاً ارزان است، " +
    "از آگهی‌ای که خودش مشکل دارد.",
  model_text:
    "پیدا کردن آگهی‌هایی که متنشان مدلی را می‌گوید و در کاتالوگ زیر مدل دیگری ثبت شده‌اند. " +
    "این مدل هرگز کاتالوگ را تغییر نمی‌دهد؛ فقط فهرستی برای بررسی انسانی می‌سازد. " +
    "نام مدل پیش از آموزش از متن حذف می‌شود: عنوان آگهی در باما همان رشته‌ای است که ردیف کاتالوگ از آن ساخته شده، " +
    "پس اگر حذف نشود مدل جواب را از روی صورت‌مسئله می‌خواند و بدون اینکه چیزی یاد گرفته باشد نمره‌ی کامل می‌گیرد.",
  value_tier:
    "دسته‌بندی آگهی‌های هر تیپ به لایه‌های ارزشی — انتهای ارزان و پرکارکرد تا انتهای تمیز و کم‌کارکرد.",
};

/**
 * A plain number for a `td.num` cell.
 *
 * Not `fa()`: that one renders Persian digits and is for prose. These sit in
 * `tabular-nums` columns next to values from `pct()` and `toman()`, which are
 * Latin, and `ui.tsx` records what happens when the two are mixed inside one
 * card — «۷ روز» on a chip above "30 روز" in the label beneath it.
 */
function num(value: number | null | undefined, digits = 3): string {
  return value == null ? "—" : value.toFixed(digits).replace(/\.?0+$/, "");
}

/** Metrics that mean something to a reader, in the order they should be read. */
const HEADLINE: Record<string, { key: string; label: string; render: (v: number) => string }[]> = {
  price: [
    { key: "interval_coverage_pct", label: "پوشش بازه (هدف: ۸۰٪)", render: (v) => pct(v, 1) },
    { key: "mape", label: "خطای میانگین مدل", render: (v) => pct(v, 2) },
    { key: "baseline_mape", label: "خطای روش آماری فعلی", render: (v) => pct(v, 2) },
    { key: "median_interval_width_pct", label: "پهنای معمول بازه", render: (v) => pct(v, 1) },
  ],
  sell_fast: [
    { key: "brier", label: "خطای برایر مدل", render: (v) => num(v, 4) },
    { key: "brier_baseline", label: "خطای برایر حدس پایه", render: (v) => num(v, 4) },
    { key: "roc_auc", label: "AUC", render: (v) => num(v, 3) },
    { key: "base_rate", label: "نرخ پایه", render: (v) => pct(v * 100, 1) },
  ],
  anomaly: [],
  model_text: [
    { key: "macro_f1", label: "ماکرو F1", render: (v) => num(v, 3) },
    { key: "accuracy", label: "دقت", render: (v) => pct(v * 100, 1) },
    { key: "classes", label: "تعداد مدل‌ها", render: (v) => num(v, 0) },
  ],
  value_tier: [
    { key: "mean_silhouette", label: "میانگین سیلوئت", render: (v) => num(v, 3) },
    { key: "variants_fitted", label: "تیپ‌های دسته‌بندی‌شده", render: (v) => num(v, 0) },
  ],
};

const STATUS: Record<string, { label: string; hint: string; Icon: typeof CheckCircle2 }> = {
  active: {
    label: "در حال استفاده",
    hint: "خروجی این مدل روی صفحه‌ها دیده می‌شود.",
    Icon: CheckCircle2,
  },
  shadow: {
    label: "در سایه",
    hint: "آموزش دیده و سنجیده شده، اما از دروازه‌ی ارتقا رد نشده — خروجی‌اش جایی نمایش داده نمی‌شود.",
    Icon: CircleDashed,
  },
  retired: {
    label: "بازنشسته",
    hint: "نسخه‌ی تازه‌تری جایش را گرفته است.",
    Icon: XCircle,
  },
};

/** The gate's verdict, in words. The machine reason stays visible beside it. */
const GATE_REASON: Record<string, string> = {
  beats_incumbent_and_baseline: "هم از نسخه‌ی قبلی بهتر بود و هم از روش آماری فعلی.",
  loses_to_baseline: "از نسخه‌ی قبلی بهتر بود اما از روش آماری فعلی نه — پس ارتقا داده نشد.",
  loses_to_incumbent: "از روش آماری بهتر بود اما از نسخه‌ای که هم‌اکنون فعال است نه.",
  loses_to_both: "نه از نسخه‌ی فعلی بهتر بود و نه از روش آماری.",
  interval_coverage_off_target:
    "دقت نقطه‌ای‌اش خوب بود، اما بازه‌ای که رسم می‌کرد با واقعیت نمی‌خواند — " +
    "بازه‌ای که ادعا می‌کند ۸۰٪ خودروها را در بر می‌گیرد و نمی‌گیرد، از نداشتنِ بازه بدتر است.",
  no_measurable_lift: "روی داده‌ی کنارگذاشته‌شده چیزی برای سنجیدن پیدا نشد.",
  no_challenger_metric: "معیار سنجشی برای این نسخه محاسبه نشد.",
};

function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? STATUS.retired;
  const tone = status === "active" ? "ok" : status === "shadow" ? "warn" : "";
  return (
    <span className={`badge ${tone}`} title={s.hint}>
      <s.Icon size={11} /> {s.label}
    </span>
  );
}

/**
 * The promotion decision, drawn as a comparison rather than as a verdict.
 *
 * Three numbers side by side is the whole story: what this version scored, what
 * the version it would replace scored, and what the plain statistical method
 * scored. A challenger has to beat both to go live.
 */
function Gate({ promotion }: { promotion?: Promotion }) {
  if (!promotion) return null;
  const better = promotion.lower_is_better === false ? "بالاتر بهتر" : "پایین‌تر بهتر";
  
  return (
    <div className="stack-sm">
      <div className="row">
        <strong className={promotion.promote ? "up" : "warn"}>
          {promotion.promote ? "ارتقا یافت" : "ارتقا نیافت"}
        </strong>
        <span className="muted">
          {GATE_REASON[promotion.reason] ?? promotion.reason}
        </span>
      </div>
      <table className="mini-table">
        <tbody>
          <tr>
            <td>این نسخه</td>
            <td className="num">{num(promotion.challenger, 4)}</td>
          </tr>
          <tr>
            <td>نسخه‌ی فعال قبلی</td>
            <td className="num">{num(promotion.incumbent, 4)}</td>
          </tr>
          <tr>
            <td>روش آماری (بدون یادگیری)</td>
            <td className="num">{num(promotion.baseline, 4)}</td>
          </tr>
        </tbody>
      </table>
      <p className="muted text-[11px]">
        {better}. برای جایگزینی باید از هر دو بهتر باشد، نه فقط از نسخه‌ی قبلی —
        وگرنه زنجیره‌ی مدل‌ها از روشی ساده‌تر که همیشه بهتر بوده فاصله می‌گیرد.
      </p>
    </div>
  );
}

/**
 * The reliability curve, as a table rather than a chart.
 *
 * Ten rows of two numbers do not need a canvas, and a table is readable by a
 * screen reader, copyable, and printable. What matters is whether the two
 * columns track each other: when the model says ۷۰٪, does it happen ۷۰٪ of the
 * time? A model that is confidently wrong is worse than one that is unsure and
 * says so, because a threshold set on the first is set on a lie.
 */
function Reliability({ curve }: { curve: ReliabilityBin[] }) {
  if (!curve?.length) return null;
  return (
    <div className="stack-sm">
      <h4 className="card-title">منحنی درستیِ احتمال</h4>
      <table className="mini-table">
        <thead>
          <tr>
            <th>احتمال اعلام‌شده</th>
            <th>آنچه واقعاً رخ داد</th>
            <th>تعداد</th>
          </tr>
        </thead>
        <tbody>
          {curve.map((b) => (
            <tr key={b.bin_lower}>
              <td className="num">{pct(b.mean_predicted * 100, 1)}</td>
              <td className="num">{pct(b.observed * 100, 1)}</td>
              <td className="num muted">{num(b.n, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted text-[11px]">
        هرچه دو ستون به هم نزدیک‌تر باشند، عددی که مدل اعلام می‌کند معنای واقعی‌تری
        دارد. این مهم‌تر از «درصد پاسخ‌های درست» است: با نرخ پایه‌ی حدود ۲۰٪،
        مدلی که همیشه «نه» بگوید ۸۰٪ درست است و هیچ چیز مفیدی نگفته.
      </p>
    </div>
  );
}

function Importance({ rows }: { rows: { feature: string; gain_pct: number }[] }) {
  if (!rows?.length) return null;
  return (
    <div className="stack-sm">
      <h4 className="card-title">چه چیزی بیشترین اثر را داشت</h4>
      <table className="mini-table">
        <tbody>
          {rows.slice(0, 8).map((r) => (
            <tr key={r.feature}>
              <td><code>{r.feature}</code></td>
              <td className="num">{pct(r.gain_pct, 1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ModelSection({ card }: { card: ModelCard }) {
  const m = card.metrics ?? {};
  const headline = (HEADLINE[card.name] ?? []).filter(
    (h) => typeof m[h.key] === "number",
  );
  const precision = m.precision_at_k as
    | { precision: number | null; base_rate: number | null; lift: number | null }
    | undefined;

  return (
    <Card
      title={`${TITLE[card.name] ?? card.label} — نسخه ${fa(card.version)}`}
      action={<StatusBadge status={card.status} />}
    >
      <div className="stack">
        <p>{PURPOSE[card.name]}</p>

        {headline.length > 0 && (
          <table className="mini-table">
            <tbody>
              {headline.map((h) => (
                <tr key={h.key}>
                  <td>{h.label}</td>
                  <td className="num">{h.render(m[h.key] as number)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {card.name === "price" && typeof m.conformal_widening_pct === "number" && (
          <p className="muted text-[11px]">
            بازه‌ی خام مدل به‌اندازه‌ی{" "}
            <b>{pct(m.conformal_widening_pct as number, 1)}</b> گشاد شده است. بازه‌های
            خامِ رگرسیون چندکی روی داده‌ی دیده‌نشده همیشه از آنچه باید تنگ‌ترند؛ این
            اصلاح از روی داده‌ی کالیبراسیون اندازه‌گیری می‌شود و هیچ فرضی درباره‌ی
            شکل توزیع نمی‌گذارد.
          </p>
        )}

        {precision && precision.lift != null && (
          <table className="mini-table">
            <tbody>
              <tr>
                <td>دقت روی نشان‌دارترین آگهی‌ها</td>
                <td className="num">{pct((precision.precision ?? 0) * 100, 1)}</td>
              </tr>
              <tr>
                <td>نرخ پایه</td>
                <td className="num">{pct((precision.base_rate ?? 0) * 100, 1)}</td>
              </tr>
              <tr>
                <td>نسبت به تصادف (باید بالای ۱ باشد)</td>
                <td className="num">{num(precision.lift, 2)}</td>
              </tr>
            </tbody>
          </table>
        )}

        <Reliability curve={(m.reliability_curve as ReliabilityBin[]) ?? []} />
        <Importance
          rows={(m.feature_importance as { feature: string; gain_pct: number }[]) ?? []}
        />
        <Gate promotion={m.promotion} />

        <details>
          <summary className="muted">جزئیات فنی</summary>
          <div className="stack-sm" style={{ marginTop: 8 }}>
            <table className="mini-table">
              <tbody>
                <tr>
                  <td>الگوریتم</td>
                  <td dir="ltr"><code>{card.algorithm}</code></td>
                </tr>
                <tr>
                  <td>تعداد ردیف آموزش</td>
                  <td className="num">{card.training_rows.toLocaleString("en-US")}</td>
                </tr>
                <tr>
                  <td>آموزش‌دیده تا</td>
                  <td>
                    {card.trained_through
                      ? new Date(card.trained_through).toLocaleDateString("fa-IR")
                      : "—"}
                  </td>
                </tr>
                <tr>
                  <td>تاریخ آموزش</td>
                  <td>{new Date(card.trained_at).toLocaleDateString("fa-IR")}</td>
                </tr>
              </tbody>
            </table>
            {card.features.length > 0 && (
              <p className="muted text-[11px]" dir="ltr">
                <code>{card.features.join(", ")}</code>
              </p>
            )}
            {card.notes && <p className="muted text-[11px]"><Fa>{card.notes}</Fa></p>}
          </div>
        </details>
      </div>
    </Card>
  );
}

export function Methodology() {
  const models = useQuery({
    queryKey: ["ml-models"],
    queryFn: ({ signal }) => api.get<ModelsResponse>("/api/ml/models/", signal),
  });

  return (
    <div className="stack">
      <Card title="روش کار">
        <div className="stack">
          <p>
            هر عددی روی این سایت از یکی از دو مسیر می‌آید. مسیر اول آماری است:
            میانه‌ی قیمت آگهی‌های هم‌گروه، جایی که «هم‌گروه» یعنی همان مدل، همان
            تیپ و همان سال ساخت. این مسیر مبنای درصد تخفیفی است که روی کارت‌ها
            می‌بینید و عمداً چیز پیچیده‌ای نیست: می‌شود با چشم بررسی‌اش کرد.
          </p>
          <p>
            مسیر دوم مدل‌های یادگیرنده‌اند و <b>جای مسیر اول را نمی‌گیرند</b> —
            کنارش می‌نشینند. دلیلش ساده است: کلید هم‌گروهی چیزی درباره‌ی کارکرد،
            وضعیت بدنه، شهر یا نوع فروشنده نمی‌داند، و این ستون‌ها روی قیمت اثر
            دارند. مدل‌ها این‌ها را می‌بینند، اما هر خروجی‌شان همراه با تفکیک
            سهم هر ویژگی ارائه می‌شود تا بتوان دو روایت مستقل از یک خودرو را
            کنار هم گذاشت.
          </p>
          <p className="muted">
            <Info size={13} /> هیچ مدلی تنها به این دلیل که تازه‌تر است فعال
            نمی‌شود. هر نسخه روی داده‌ای سنجیده می‌شود که در آموزش ندیده — و
            بریدن داده بر اساس <b>زمان</b> است نه تصادفی، چون تقسیم تصادفی به
            مدل اجازه می‌دهد آینده را ببیند. سپس باید هم از نسخه‌ی فعلی بهتر
            باشد و هم از روش آماری. اگر نشد، در سایه می‌ماند و همین‌جا نوشته
            می‌شود که چرا.
          </p>
        </div>
      </Card>

      <Async
        query={models}
        empty="هنوز هیچ مدلی آموزش ندیده است."
        shape="table"
      >
        {(data) => (
          <div className="stack">
            {typeof data.scored_ads === "number" && (
              <p className="muted">
                در حال حاضر <b>{fa(data.scored_ads)}</b> آگهی با مدل‌های فعال
                امتیازدهی شده‌اند.
              </p>
            )}
            {/* Say what is not shown. The page keeps the live version of each
                model plus its few most recent challengers; without this line a
                reader has no way to tell that the list is a window rather than
                the whole training history. */}
            {typeof data.shown === "number"
              && typeof data.trained_total === "number"
              && data.trained_total > data.shown && (
              <p className="muted">
                <Info size={13} /> از مجموع <b>{fa(data.trained_total)}</b> نسخه‌ی
                آموزش‌دیده، <b>{fa(data.shown)}</b> نسخه نمایش داده می‌شود: نسخه‌ی
                فعال هر مدل، به‌همراه{" "}
                {typeof data.history_per_model === "number"
                  ? <>{fa(data.history_per_model)} تلاش اخیرِ</>
                  : <>چند تلاش اخیرِ</>}{" "}
                آن.
              </p>
            )}
            {data.models.map((card) => (
              <ModelSection key={`${card.name}-${card.version}`} card={card} />
            ))}
            <Provenance envelope={data} />
          </div>
        )}
      </Async>
    </div>
  );
}
