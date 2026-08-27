/**
 * The palette's own test. Run with `npm run check:contrast`.
 *
 * Two rules this product cannot break, both invisible to a build:
 *
 *   1. Every colour used as small text has to clear 4.5:1 on every surface it
 *      can land on. The warning colours are the ones that matter most — a
 *      staleness badge nobody can read is worse than no badge.
 *   2. Accent, up, down and warn have to stay far enough apart *in hue* to read
 *      as different signals. Contrast ratio cannot see this: two colours of
 *      equal lightness score ~1.0 against each other whether they are teal and
 *      green or teal and teal. That is exactly the failure mode a teal accent
 *      sitting next to a green "price up" invites, so hue is checked directly.
 *
 * Tokens are parsed out of src/styles.css rather than restated here. A checker
 * holding its own copy of the palette is a checker that goes on passing after
 * the palette moves.
 */
import { readFileSync } from "node:fs";

const CSS = new URL("../src/styles.css", import.meta.url);

const hex = (h) => h.replace("#", "").match(/../g).map((x) => parseInt(x, 16));
const lin = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
const lum = (h) => { const [r, g, b] = hex(h).map(lin); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

/** color-mix(in srgb, <a> <pct>%, <b>) — the form styles.css uses for badge fills. */
const mix = (a, pct, b) => {
  const [A, B] = [hex(a), hex(b)];
  return "#" + A.map((v, i) =>
    Math.round((v * pct + B[i] * (100 - pct)) / 100).toString(16).padStart(2, "0")).join("");
};

const hue = (h) => {
  const [r, g, b] = hex(h).map((v) => v / 255);
  const mx = Math.max(r, g, b), d = mx - Math.min(r, g, b);
  if (!d) return 0;
  const x = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return (x * 60 + 360) % 360;
};
const hueGap = (a, b) => { const d = Math.abs(hue(a) - hue(b)); return Math.round(Math.min(d, 360 - d)); };

/** Pull one `:root`-ish block's hex tokens out of the stylesheet. */
function tokens(css, selector) {
  const at = css.indexOf(selector);
  if (at < 0) throw new Error(`${selector} not found in styles.css`);
  const body = css.slice(at, css.indexOf("}", at));
  const out = {};
  for (const [, name, value] of body.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{3,8})\s*;/g)) {
    out[name] = value.length === 4
      ? "#" + value.slice(1).split("").map((c) => c + c).join("")
      : value.slice(0, 7);
  }
  return out;
}

const css = readFileSync(CSS, "utf8");
const themes = {
  light: tokens(css, ":root {"),
  dark: tokens(css, ':root[data-theme="dark"]'),
};

// [foreground, background, minimum]. 4.5 is the small-text bar. The border check
// is a graphics bar: a 1px rule only has to be perceptible, not readable.
const TEXT_ON_SURFACE = [];
for (const fg of ["text", "muted", "accent", "up", "down", "warn"]) {
  for (const bg of ["bg", "panel", "panel-2"]) {
    TEXT_ON_SURFACE.push([fg, bg, fg === "text" ? 7 : 4.5]);
  }
}
const PAIRS = [
  ...TEXT_ON_SURFACE,
  ["accent-fg", "accent", 4.5],   // a filled primary button
  ["accent", "accent-soft", 4.5], // an active filter chip
  ["warn", "warn-soft", 4.5],     // the staleness badge
  ["up-fg", "up", 4.5],           // the deal ribbon
  ["warn-fg", "warn", 4.5],       // the deal ribbon, once the gap is suspect
  ["border", "panel", 1.2],
];

// Signals that must not be mistaken for one another.
const HUES = [["accent", "up"], ["accent", "down"], ["up", "warn"], ["warn", "down"]];
const MIN_HUE_GAP = 30;

let failures = 0;
const report = (ok, label, value, need) => {
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"} ${label.padEnd(28)} ${String(value).padStart(6)} (need ${need})`);
};

for (const [name, t] of Object.entries(themes)) {
  console.log(`\n--- ${name} ---`);
  for (const [fg, bg, min] of PAIRS) {
    if (!t[fg] || !t[bg]) { report(false, `${fg} on ${bg} (missing token)`, "-", min); continue; }
    const r = ratio(t[fg], t[bg]);
    report(r >= min, `${fg} on ${bg}`, r.toFixed(2), min);
  }
  // The two badge fills styles.css builds with color-mix() at use time.
  for (const key of ["up", "down"]) {
    const r = ratio(t[key], mix(t[key], 18, t.panel));
    report(r >= 4.5, `${key} on its 18% badge fill`, r.toFixed(2), 4.5);
  }
  for (const [a, b] of HUES) {
    const g = hueGap(t[a], t[b]);
    report(g >= MIN_HUE_GAP, `hue gap ${a}/${b}`, `${g}deg`, `${MIN_HUE_GAP}deg`);
  }
}

console.log(failures ? `\n${failures} FAILING` : "\nall pass");
process.exit(failures ? 1 : 0);
