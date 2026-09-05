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
import { scopeKey } from "./components/FollowButton";
import { qs } from "./filters";
import { pct, toman } from "./ui";

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
  // The same thresholds `apps/core/notify.py:toman` applies, because the deal
  // board and the Telegram message quote one price and disagreeing about its
  // magnitude is how a 2.2B car once read as "220M".
  it.each([
    [null, "—"],
    [undefined, "—"],
    [0, "0"],
    [999_999, "999,999"],
    [1_000_000, "1M"],
    [2_500_000, "3M"],
    [999_000_000, "999M"],
    [1_000_000_000, "1.00B"],
    [2_200_000_000, "2.20B"],
  ])("%s -> %s", (value, expected) => {
    expect(toman(value)).toBe(expected);
  });

  it("switches unit exactly at the boundary, not near it", () => {
    expect(toman(999_999_999)).toBe("1000M");
    expect(toman(1_000_000_000)).toBe("1.00B");
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
