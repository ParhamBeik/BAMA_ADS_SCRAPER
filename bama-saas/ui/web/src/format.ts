/**
 * Pure formatters. No React, no imports, no side effects.
 *
 * Lifted out of `ui.tsx`, which had grown into four unrelated concerns in one
 * 900-line module: these, a Persian error dictionary, generic layout
 * primitives, and domain widgets carrying product decisions. Ten to thirteen
 * files import each of these, and none of them wanted a React module to do it.
 *
 * The numeral system is the thing to get right here. `num`, `toman`, `pct` and
 * `km` stay in Latin digits because they sit in `tabular-nums` columns beside
 * Latin magnitude suffixes ("3.90B"); `fa` is the Persian-digit one and is for
 * prose. Mixing them inside one card is what put «۷ روز» on a chip and
 * "30 روز" in the label directly beneath it.
 */

/** Persian and Arabic-Indic digits as ASCII, plus the separators people paste. */
export function latinDigits(input: string): string {
  return input
    .replace(/[۰-۹]/g, (ch) => String(ch.charCodeAt(0) - 0x06f0))
    .replace(/[٠-٩]/g, (ch) => String(ch.charCodeAt(0) - 0x0660))
    .replace(/[,٬\s]/g, "");
}

export function parseBudget(input: string): number | null {
  const amount = Number(latinDigits(input).replace(/[^\d]/g, ""));
  return Number.isFinite(amount) && amount > 0 ? amount : null;
}

export function toman(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `${Math.round(value / 1_000_000)}M`;
  return num(value);
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
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString("fa-IR");
}

/**
 * A number set in Persian digits, for prose.
 *
 * `toman`, `pct` and `km` stay Latin on purpose — they sit in `tabular-nums`
 * columns beside Latin magnitude suffixes ("3.90B"). Sentences are the other
 * case, and mixing the two inside one card is what put «۷ روز» on a chip and
 * "30 روز" in the label directly beneath it.
 */
export function fa(value: number | null | undefined): string {
  return value == null ? "—" : value.toLocaleString("fa-IR", { useGrouping: true });
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
 * Western digits throughout, like `toman` and `pct`: numbers on this site sit
 * in `tabular-nums` columns and mix with Latin magnitude suffixes ("3.90B"),
 * so digits stay Latin rather than switching numeral systems mid-line.
 */
export function km(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value === 0) return "0 km";
  if (value < 1000) return `${num(value)} km`;
  return `${num(Math.round(value / 1000))}k km`;
}
