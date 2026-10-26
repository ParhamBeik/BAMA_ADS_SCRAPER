/**
 * Pure formatters. No React, no imports, no side effects.
 *
 * Lifted out of `ui.tsx`, which had grown into four unrelated concerns in one
 * 900-line module: these, a Persian error dictionary, generic layout
 * primitives, and domain widgets carrying product decisions. Ten to thirteen
 * files import each of these, and none of them wanted a React module to do it.
 *
 * The numeral system is the thing to get right here: every number on the site
 * is set in Latin digits, and the words around it — units, magnitudes, dates'
 * month names — stay Persian. Mixing numeral systems inside one card is what
 * put «۷ روز» on a chip and "30 روز" in the label directly beneath it.
 */

/** Persian and Arabic-Indic digits as ASCII, plus the separators people paste. */
export function latinDigits(input: string): string {
  return input
    .replace(/[۰-۹]/g, (ch) => String(ch.charCodeAt(0) - 0x06f0))
    .replace(/[٠-٩]/g, (ch) => String(ch.charCodeAt(0) - 0x0660))
    .replace(/[,٬\s]/g, "");
}

/** The way prices are said, not only typed: «۸۰۰ میلیون», «1.5 میلیارد»,
 *  «50 هزار». Digits alone pass through. The word used to be dropped, so
 *  «۸۰۰ میلیون» filtered for 800 tomans. */
const MAGNITUDES: [RegExp, number][] = [[/میلیارد/, 1e9], [/میلیون/, 1e6], [/هزار/, 1e3]];

export function parseAmount(input: string): number | null {
  const text = latinDigits(input).replace(/٫/g, ".").replace(/(\d)[,٬](?=\d{3})/g, "$1");
  const digits = text.match(/\d+(?:\.\d+)?/);
  if (!digits) return null;
  const scale = MAGNITUDES.find(([word]) => word.test(text))?.[1] ?? 1;
  return Math.round(Number(digits[0]) * scale);
}

export function parseBudget(input: string): number | null {
  const amount = parseAmount(input);
  return amount != null && amount > 0 ? amount : null;
}

export function toman(value: number | null | undefined): string {
  if (value == null) return "—";
  // Latin digits, Persian magnitude words. Two decimals keep a 50M-toman gap
  // visible between billions; Number() drops the zeros that carry nothing.
  if (value >= 1_000_000_000) return `${Number((value / 1_000_000_000).toFixed(2))} میلیارد`;
  if (value >= 1_000_000) return `${Math.round(value / 1_000_000)} میلیون`;
  return num(value);
}

/** "1.13 – 1.37 میلیارد": one unit word for a range whose ends share it. */
export function tomanRange(lo: number | null | undefined, hi: number | null | undefined): string {
  if (lo == null || hi == null) return "—";
  const [a, b] = [toman(lo), toman(hi)];
  const unit = (t: string) => t.split(" ")[1];
  return unit(a) && unit(a) === unit(b) ? `${a.split(" ")[0]} – ${b}` : `${a} – ${b}`;
}

export function pct(value: number | null | undefined, digits = 1): string {
  return value == null ? "—" : `${value.toFixed(digits)}%`;
}

/**
 * A grouped count in Latin digits — "12,480".
 *
 * The plain one, and the one almost every number on this site wants: counts of
 * ads, cohorts, brands, models. It was written inline as a raw
 * `toLocaleString("en-US")` at 45 places across 11 files, with `pages/Control`
 * keeping a private copy of it as well.
 *
 * Latin rather than Persian digits, and that is the whole reason this is not
 * `fa`: these sit in `tabular-nums` columns next to Latin magnitude suffixes
 * from `toman` ("3.90B"), and switching numeral systems mid-column is exactly
 * what `fa`'s own note warns against. `fa` stays the one for prose.
 *
 * Null yields "" rather than an em dash, because the call sites that can pass
 * null were written with `?.` and rendered nothing at all. React renders "" and
 * undefined identically, so those keep the output they had.
 */
export function num(value: number | null | undefined): string {
  return value == null ? "" : value.toLocaleString("en-US");
}

/**
 * A stored timestamp as a Jalali calendar date — "۱۴۰۵/۶/۲۴".
 *
 * Date only, no time: these label when something was last seen, trained or
 * repriced, and the clock time is noise at that granularity. Written out as
 * `new Date(x).toLocaleDateString("fa-IR")` at six sites across three pages.
 *
 * Deliberately *not* merged with the other three Persian date renderings in
 * this app, which produce different strings and were checked against each
 * other rather than assumed equivalent:
 *
 *   - `Chart.axisDate`  month and day only, for a time axis tick
 *   - `Provenance`      full date *and* time, for an "as of" stamp
 *   - `Control.when`    date and time pinned to Asia/Tehran, because those are
 *                       raw UTC rows on a Tehran operator screen
 *
 * An invalid date renders as the original value rather than "Invalid Date",
 * matching what the call sites did by guarding on a truthy input.
 */
export function faDate(value: string | number | Date | null | undefined): string {
  if (!value) return "—";
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString("fa-IR-u-nu-latn");
}

/**
 * A number inside Persian prose. Latin digits like everything else (see the
 * header); it differs from `num` only in rendering null as a dash.
 */
export function fa(value: number | null | undefined): string {
  return value == null ? "—" : num(value);
}

/**
 * "0.1 ساعت پیش" is not how anyone says four minutes.
 *
 * Hours below one are read in minutes and anything under a minute is "just
 * now"; the API reports a decimal hour count because that is the natural unit
 * for staleness, not for prose.
 */
export function since(hours: number | null | undefined): string {
  if (hours == null) return "—";
  const minutes = Math.round(hours * 60);
  if (minutes < 1) return "همین الان";
  if (minutes < 60) return `${fa(minutes)} دقیقه پیش`;
  if (hours < 24) return `${fa(Math.round(hours))} ساعت پیش`;
  return `${fa(Math.round(hours / 24))} روز پیش`;
}

/**
 * Odometer, at card density.
 *
 * Rounding to thousands rendered every new car as "0k km", which read as
 * missing data rather than as a zero-kilometre car — so the two stay distinct
 * strings ("0 km" vs "—").
 *
 * Latin digits, Persian unit words — "156 هزار کیلومتر".
 */
export function km(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value < 1000) return `${num(value)} کیلومتر`;
  return `${num(Math.round(value / 1000))} هزار کیلومتر`;
}

/** Bama's body status, grouped the way `quality.condition_band` groups it on
 *  the server. One list: three screens used to carry their own wording. */
export const CONDITION_BANDS = [
  { value: "clean", label: "بدون رنگ" },
  { value: "cosmetic", label: "لکه / خط و خش" },
  { value: "painted", label: "رنگ‌شده" },
  { value: "structural", label: "تعویض / تصادفی" },
] as const;

/** Peer groups also split 0 km cars out (`quality.peer_band`); body filters do not. */
export function conditionLabel(band: unknown): string {
  if (band === "new") return "صفر کیلومتر";
  return CONDITION_BANDS.find((c) => c.value === band)?.label ?? String(band ?? "");
}

/** `parsing.JALALI_GREGORIAN_OFFSET`: how every stored ad derives one calendar
 *  from the other, so this matches the server exactly. */
const JALALI_GREGORIAN_OFFSET = 621;

/**
 * A model year in both calendars, the ad's own first: "1402 · 2023" for a
 * domestic car, "2023 · 1402" for an import listed in Gregorian.
 */
export function modelYear(jalali: number | null | undefined, calendar?: string | null): string {
  if (jalali == null) return "—";
  // Jalali first on every card. It used to follow the ad's own calendar, so
  // neighbouring cards read «1402 · 2023» and «2025 · 1404».
  void calendar;
  return `${jalali} · ${jalali + JALALI_GREGORIAN_OFFSET}`;
}

/** A date in both calendars: "1405/7/12 · 4 Oct 2026". */
export function bothDates(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const gregorian = date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  return `${faDate(date)} · ${gregorian}`;
}

/** The trim, when the card title does not already say it. */
export function trimNote(title: string | null | undefined, trim: string | null | undefined): string {
  const t = (trim ?? "").trim();
  return t && !(title ?? "").includes(t) ? t : "";
}

/** Brand, model and trim as one name, without the words the trim repeats.
 *  Bama's trim names restate the model's gearbox and engine, so the plain join
 *  read «کیا ریو دنده ای دنده ای» and «ام وی ام 110 دنده ای 4 سیلندر دنده ای». */
export function carName(brand: string, model: string, trim?: string | null): string {
  const said = new Set(model.split(/\s+/));
  const extra = (trim ?? "").split(/\s+/).filter((w) => w && !said.has(w)).join(" ");
  return [brand, model, extra].filter(Boolean).join(" ");
}
