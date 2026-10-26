/**
 * The pure logic behind the screens, and one deliberate cross-language mirror.
 *
 * Kept to functions with no React in them on purpose. Rendering tests would
 * need a DOM, a query client and a router around every component, and would
 * mostly assert that Radix still renders Radix. What is worth pinning here is
 * the arithmetic and the string formats that something *else* also depends on:
 * `scopeKey` mirrors a server function, and `toman` has to agree with the
 * Telegram notifier that quotes the same price.
 */
import { describe, expect, it } from "vitest";
import { onePerSeller, verdictNote } from "./components/DealCard";
import { scopeKey } from "./components/FollowButton";
import { qs } from "./filters";
import { mileageLabel } from "./components/FilterPanel";
import { carName, fa, modelYear, parseAmount, parseBudget, pct, toman, tomanRange, trimNote } from "./format";

it("parses Persian and Arabic-Indic budget input without dropping its digits", () => {
  expect(parseBudget("۱٬۲۰۰٬۰۰۰٬۰۰۰")).toBe(1_200_000_000);
  expect(parseBudget("١٢٠٠٠٠٠٠٠٠")).toBe(1_200_000_000);
  expect(parseBudget("abc")).toBeNull();
});

it("reads a price the way it is said", () => {
  expect(parseAmount("۸۰۰ میلیون")).toBe(800_000_000);
  expect(parseAmount("1٫5 میلیارد")).toBe(1_500_000_000);
  expect(parseAmount("50 هزار")).toBe(50_000);
  expect(parseAmount("12.5")).toBe(13);
  expect(parseAmount("میلیون")).toBeNull();
});

it("names a mileage pair as one filter", () => {
  expect(mileageLabel("0", "0")).toBe("صفر کیلومتر");
  expect(mileageLabel("1", "5000")).toBe("کارکرد زیر 5,000");
  expect(mileageLabel("100001", undefined)).toBe("کارکرد بالای 100,001");
  expect(mileageLabel(undefined, undefined)).toBeNull();
});

/**
 * Exactly the strings `ScopedToACar.build_scope_key` produces.
 *
 * This is the mirror, and it is the reason the table is literal rather than
 * generated: the unique constraint that stops a user following «all of Peugeot»
 * twice is on the server's key, and this client decides whether to draw the
 * button as "followed" by comparing against it. If the two derivations drift,
 * the button reads "follow" for something already followed and pressing it
 * returns the existing row — a bug with no error to explain it.
 *
 * `tests/test_api.py::test_the_scope_key_format_is_the_one_the_client_mirrors`
 * asserts this same table on the Python side, so a change to either derivation
 * fails on one side or the other.
 */
const SCOPE_KEYS: ReadonlyArray<[Record<string, string>, string]> = [
  [{}, "market"],
  [{ brand: "peugeot" }, "brand:peugeot"],
  [{ model: "12" }, "model:12"],
  [{ brand: "peugeot", model: "12" }, "brand:peugeot/model:12"],
  [{ brand: "peugeot", model: "12", variant: "7" }, "brand:peugeot/model:12/variant:7"],
  [
    { brand: "peugeot", model: "12", variant: "7", year: "1401" },
    "brand:peugeot/model:12/variant:7/year:1401",
  ],
  // Narrowest last, and gaps simply omitted — which is what makes a prefix
  // match find everything under a brand.
  [{ brand: "peugeot", year: "1401" }, "brand:peugeot/year:1401"],
  [{ model: "12", year: "1401" }, "model:12/year:1401"],
];

describe("scopeKey", () => {
  it.each(SCOPE_KEYS)("%o -> %s", (scope, expected) => {
    expect(scopeKey(scope)).toBe(expected);
  });

  it("treats an empty string as an absent field, not as a value", () => {
    // A cleared picker writes "" into the URL rather than removing the param.
    // Read as a value it would produce "brand:" and match nothing on the server.
    expect(scopeKey({ brand: "", model: "12" })).toBe("model:12");
  });

  it("never returns an empty key", () => {
    // "" would compare equal to nothing and the button would render for the
    // whole market, where every alert matches.
    expect(scopeKey({})).toBe("market");
  });
});

describe("toman", () => {
  // The same thresholds `apps/core/notify.py:toman` applies (its English
  // Telegram text keeps "2.20B"; the UI spells the unit in Persian), because the deal
  // board and the Telegram message quote one price and disagreeing about its
  // magnitude is how a 2.2B car once read as "220M".
  it.each([
    [null, "—"],
    [undefined, "—"],
    [0, "0"],
    [999_999, "999,999"],
    [1_000_000, "1 میلیون"],
    [2_500_000, "3 میلیون"],
    [999_000_000, "999 میلیون"],
    [1_000_000_000, "1 میلیارد"],
    [2_200_000_000, "2.2 میلیارد"],
    [10_950_000_000, "10.95 میلیارد"],
  ])("%s -> %s", (value, expected) => {
    expect(toman(value)).toBe(expected);
  });

  it("switches unit exactly at the boundary, not near it", () => {
    expect(toman(999_999_999)).toBe("1000 میلیون");
    expect(toman(1_000_000_000)).toBe("1 میلیارد");
  });
});

describe("pct", () => {
  it.each([
    [null, "—"],
    [0, "0.0%"],
    [12.34, "12.3%"],
    [-5, "-5.0%"],
  ])("%s -> %s", (value, expected) => {
    expect(pct(value)).toBe(expected);
  });

  it("keeps zero distinct from absent", () => {
    // A 0% move is a fact; a missing one is not, and an em dash is how the
    // screens say so. `value == null` rather than a falsy check is what keeps
    // these apart.
    expect(pct(0)).not.toBe(pct(null));
  });
});

describe("qs", () => {
  it("omits undefined, null and empty values", () => {
    expect(qs({ a: 1, b: undefined, c: null, d: "", e: "x" })).toBe("?a=1&e=x");
  });

  it("returns an empty string rather than a bare question mark", () => {
    expect(qs({})).toBe("");
    expect(qs({ a: undefined })).toBe("");
  });

  it("keeps zero, which is a value", () => {
    // The same rule the backend states about mileage: "صفر کیلومتر" is 0, not
    // absent, so a falsy check here would silently drop the filter.
    expect(qs({ mileage_min: 0 })).toBe("?mileage_min=0");
  });

  it("percent-encodes Persian text", () => {
    expect(qs({ q: "پژو" })).toBe("?q=%D9%BE%DA%98%D9%88");
  });
});

describe("modelYear", () => {
  it("shows both calendars, the ad's own first", () => {
    expect(modelYear(1402)).toBe("1402 · 2023");
    expect(modelYear(1402, "gregorian")).toBe("1402 · 2023");
    expect(modelYear(null)).toBe("—");
  });
});

describe("verdictNote", () => {
  it("names the reasons a cheap car went to review", () => {
    expect(verdictNote({ deal_status: "check", deal_reasons: ["stale_price"], price_age_days: 40 }))
      .toBe(`قیمت قدیمی · قیمتِ ${fa(40)} روز پیش`);
  });
  it("states the comparison behind a deal", () => {
    expect(verdictNote({ deal_status: "deal", cheaper_than_pct: 90, fresh_peers: 20, price_age_days: 0 }))
      .toBe(`ارزان‌تر از ${fa(90)}٪ از ${fa(20)} آگهی تازه · قیمت امروز`);
  });
  it("says nothing when no peer group could judge", () => {
    expect(verdictNote({ deal_status: null })).toBeNull();
  });
});

describe("tomanRange", () => {
  it("says the shared unit once", () => {
    expect(tomanRange(1_130_000_000, 1_370_000_000)).toBe("1.13 – 1.37 میلیارد");
  });
  it("keeps both units when they differ", () => {
    expect(tomanRange(950_000_000, 1_100_000_000)).toBe("950 میلیون – 1.1 میلیارد");
  });
});

it("names a car without the words its trim repeats", () => {
  expect(carName("کیا", "ریو (مونتاژ) دنده ای", "دنده ای")).toBe("کیا ریو (مونتاژ) دنده ای");
  expect(carName("ام وی ام", "110 دنده ای", "4 سیلندر دنده ای")).toBe("ام وی ام 110 دنده ای 4 سیلندر");
  expect(carName("پژو", "206 دنده ای", "تیپ 2")).toBe("پژو 206 دنده ای تیپ 2");
  expect(carName("پژو", "206", "")).toBe("پژو 206");
});

it("keeps one car per dealer on a shortlist, and every private seller", () => {
  const rows = [
    { code: "a", dealer_id: 5175 }, { code: "b", dealer_id: 5175 },
    { code: "c", dealer_id: null }, { code: "d", dealer_id: null },
    { code: "e", dealer_id: 718 }, { code: "f", dealer_id: 5175 },
  ];
  expect(onePerSeller(rows, 6).map((r) => r.code)).toEqual(["a", "c", "d", "e"]);
  expect(onePerSeller(rows, 2).map((r) => r.code)).toEqual(["a", "c"]);
});

describe("final pass", () => {
  it("parseAmount reads grouped digits as one number", () => {
    expect(parseAmount("1,500,000,000")).toBe(1500000000);
  });
  it("trimNote hides a trim the title already names", () => {
    expect(trimNote("پژو 206 تیپ 2", "تیپ 2")).toBe("");
  });
});
