/**
 * The filter panel, shared by the deal board and the explorer.
 *
 * It replaces a flat strip of unlabelled inputs whose model picker was hidden
 * inside a `<details>` *and* disabled until a brand had been chosen — so
 * finding a 206 required first knowing it is a پژو. The picker here is a single
 * searchable box over every model, with the listing count beside each, and the
 * brand select is now the coarse filter it should have been.
 *
 * State still lives in the URL via `useFilters`, so every view stays shareable
 * and the back button keeps working. Nothing here holds a second copy.
 *
 * The scraped vocabularies below must stay byte-equal to the text stored on
 * `Ad`: the backend matches `transmission` case-sensitively.
 */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search, X } from "lucide-react";
import { api } from "./api";
import type { Paginated } from "./api";
import { qs, useFilters } from "./filters";
import { Fa, toman } from "./ui";

const TRANSMISSIONS = ["اتوماتیک", "دنده ای"];
const FUELS = ["بنزینی", "هیبریدی", "برقی", "دوگانه سوز", "پلاگین هیبرید", "بردافزا", "هیبرید ملایم", "دیزلی"];
const BODY_TYPES = ["سدان", "کراس اور", "هاچبک", "وانت", "شاسی بلند‌", "ون", "کوپه", "کروک"];

const BILLION = 1_000_000_000;
const PRICE_PRESETS: [string, number | null, number | null][] = [
  ["تا ۵۰۰ میلیون", null, 500_000_000],
  ["۵۰۰ تا ۱ میلیارد", 500_000_000, BILLION],
  ["۱ تا ۲ میلیارد", BILLION, 2 * BILLION],
  ["۲ تا ۵ میلیارد", 2 * BILLION, 5 * BILLION],
  ["بالای ۵ میلیارد", 5 * BILLION, null],
];

/** Jalali, like every year in this app — `Ad.year` mixes 1399 and 2025. */
const YEAR_PRESETS: [string, number | null][] = [
  ["۵ سال اخیر", 1400],
  ["۱۰ سال اخیر", 1395],
  ["۱۳۹۰ به بعد", 1390],
];

const MILEAGE_PRESETS: [string, number][] = [
  ["زیر ۵۰ هزار", 50_000],
  ["زیر ۱۰۰ هزار", 100_000],
  ["زیر ۲۰۰ هزار", 200_000],
];

export const FILTER_KEYS = [
  "brand", "model", "variant", "q", "price_min", "price_max",
  "year_min", "year_max", "mileage_min", "mileage_max", "transmission", "fuel",
  "body_type", "condition", "seller_type", "confidence",
];

interface Brand { slug: string; name_fa: string }
interface ModelRow {
  id: number;
  name_fa: string;
  brand_slug: string;
  brand_name: string;
  ad_count: number;
}
interface Variant { id: number; name_fa: string }

/** Which controls a screen wants. The board has no body-type question; the
 *  explorer has no confidence one. */
export interface FilterOptions {
  showSpecs?: boolean;
  showConfidence?: boolean;
  showSearch?: boolean;
}

/**
 * Search every model at once, with how many listings each has.
 *
 * Debounced, because it fires per keystroke against a counting query. The count
 * is the point: it turns "is this the right one of four similar names" into a
 * fact rather than a guess.
 */
function ModelPicker() {
  const filters = useFilters();
  const selectedId = filters.get("model");
  const brand = filters.get("brand");
  const [draft, setDraft] = useState("");
  const [term, setTerm] = useState("");
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const id = setTimeout(() => setTerm(draft), 220);
    return () => clearTimeout(id);
  }, [draft]);

  // Clicking anywhere else closes the list. Without this the list stays over
  // the results grid and swallows the next click on a card.
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const models = useQuery({
    queryKey: ["model-search", term, brand],
    queryFn: ({ signal }) =>
      api.get<ModelRow[]>(`/api/models/${qs({ q: term, brand })}`, signal),
  });

  const selected = models.data?.find((m) => String(m.id) === selectedId);

  return (
    <div className="filter-field combo" ref={box}>
      <label htmlFor="model-search">مدل خودرو</label>
      <input
        id="model-search"
        type="search"
        autoComplete="off"
        placeholder={selected ? `${selected.brand_name} ${selected.name_fa}` : "نام مدل را بنویسید…"}
        value={draft}
        onChange={(e) => { setDraft(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
        role="combobox"
        aria-expanded={open}
        aria-controls="model-options"
      />
      {open && (
        <div className="combo-list" id="model-options" role="listbox">
          {models.isLoading && <div className="combo-option">در حال جست‌وجو…</div>}
          {!models.isLoading && !models.data?.length && (
            <div className="combo-option">مدلی پیدا نشد</div>
          )}
          {selectedId && (
            <button
              type="button"
              className="combo-option"
              onClick={() => {
                filters.set({ model: null, variant: null, page: null });
                setDraft("");
                setOpen(false);
              }}
            >
              همه مدل‌ها
            </button>
          )}
          {models.data?.map((m) => (
            <button
              key={m.id}
              type="button"
              role="option"
              aria-selected={String(m.id) === selectedId}
              className="combo-option"
              onClick={() => {
                // Setting the brand too keeps the coarse select honest: it
                // would otherwise still read "همه برندها" next to one model.
                filters.set({
                  model: m.id, brand: m.brand_slug, variant: null, page: null,
                });
                setDraft("");
                setOpen(false);
              }}
            >
              <span>
                <Fa>{m.brand_name} {m.name_fa}</Fa>
              </span>
              <span className="combo-count">{m.ad_count.toLocaleString("en-US")}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function TrimPicker() {
  const filters = useFilters();
  const model = filters.get("model");
  const variants = useQuery({
    queryKey: ["variants", model],
    enabled: Boolean(model),
    queryFn: ({ signal }) => api.get<Variant[]>(`/api/models/${model}/variants/`, signal),
  });

  if (!model || !variants.data?.length) return null;
  return (
    <div className="filter-field">
      <label htmlFor="variant">تیپ</label>
      <select
        id="variant"
        value={filters.get("variant") ?? ""}
        onChange={(e) => filters.set({ variant: e.target.value || null, page: null })}
      >
        <option value="">همه تیپ‌ها</option>
        {variants.data.map((v) => (
          <option key={v.id} value={v.id}>{v.name_fa}</option>
        ))}
      </select>
    </div>
  );
}

/** A labelled min/max pair. Committed on blur, like the rest of the app —
 *  filtering per keystroke would refetch on every digit of a price. */
function RangeField({
  label, minKey, maxKey, placeholderMin, placeholderMax,
}: {
  label: string;
  minKey: string;
  maxKey: string;
  placeholderMin: string;
  placeholderMax: string;
}) {
  const filters = useFilters();
  const min = filters.get(minKey);
  const max = filters.get(maxKey);
  return (
    <div className="filter-field">
      <span>{label}</span>
      <div className="filter-row">
        <input
          key={`${minKey}-${min}`}
          type="number"
          inputMode="numeric"
          aria-label={`${label} از`}
          placeholder={placeholderMin}
          defaultValue={min ?? ""}
          onBlur={(e) => filters.set({ [minKey]: e.target.value || null, page: null })}
        />
        <span className="filter-sep">تا</span>
        <input
          key={`${maxKey}-${max}`}
          type="number"
          inputMode="numeric"
          aria-label={`${label} تا`}
          placeholder={placeholderMax}
          defaultValue={max ?? ""}
          onBlur={(e) => filters.set({ [maxKey]: e.target.value || null, page: null })}
        />
      </div>
    </div>
  );
}

function Select({
  id, label, value, onChange, anyLabel, options,
}: {
  id: string;
  label: string;
  value?: string;
  onChange: (v: string | null) => void;
  anyLabel: string;
  options: { value: string; label: string }[];
}) {
  return (
    <div className="filter-field">
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
        <option value="">{anyLabel}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}

export function FilterPanel({
  showSpecs = true,
  showConfidence = false,
  showSearch = true,
}: FilterOptions) {
  const filters = useFilters();
  const brand = filters.get("brand");

  const brands = useQuery({
    queryKey: ["brands"],
    queryFn: ({ signal }) => api.get<Paginated<Brand> | Brand[]>("/api/brands/", signal),
  });
  const brandList: Brand[] = Array.isArray(brands.data)
    ? brands.data
    : (brands.data?.results ?? []);

  const priceMin = filters.get("price_min");
  const priceMax = filters.get("price_max");
  const yearMin = filters.get("year_min");
  const mileageMax = filters.get("mileage_max");

  return (
    <section className="filter-panel" aria-label="فیلترها">
      <div className="filter-groups">
        <div className="filter-group">
          <h3 className="card-title">خودرو</h3>
          {showSearch && (
            <div className="filter-field">
              <label htmlFor="q">جست‌وجو در آگهی‌ها</label>
              <input
                id="q"
                key={filters.get("q")}
                type="search"
                placeholder="عنوان، برند یا توضیحات"
                defaultValue={filters.get("q") ?? ""}
                onBlur={(e) => filters.set({ q: e.target.value || null, page: 1 })}
              />
            </div>
          )}
          <Select
            id="brand"
            label="برند"
            anyLabel="همه برندها"
            value={brand}
            onChange={(v) => filters.set({ brand: v, model: null, variant: null, page: null })}
            options={brandList.map((b) => ({ value: b.slug, label: b.name_fa }))}
          />
          <ModelPicker />
          <TrimPicker />
        </div>

        <div className="filter-group">
          <h3 className="card-title">قیمت (تومان)</h3>
          <div className="preset-row">
            {PRICE_PRESETS.map(([label, lo, hi]) => {
              const on = String(lo ?? "") === (priceMin ?? "") &&
                         String(hi ?? "") === (priceMax ?? "");
              return (
                <button
                  key={label}
                  type="button"
                  className={`preset${on ? " on" : ""}`}
                  aria-pressed={on}
                  onClick={() =>
                    filters.set({
                      price_min: on ? null : lo, price_max: on ? null : hi, page: null,
                    })
                  }
                >
                  {label}
                </button>
              );
            })}
          </div>
          <RangeField
            label="یا بازه دلخواه"
            minKey="price_min"
            maxKey="price_max"
            placeholderMin="از"
            placeholderMax="تا"
          />
        </div>

        <div className="filter-group">
          <h3 className="card-title">سال و کارکرد</h3>
          <div className="preset-row">
            {YEAR_PRESETS.map(([label, from]) => {
              const on = String(from ?? "") === (yearMin ?? "");
              return (
                <button
                  key={label}
                  type="button"
                  className={`preset${on ? " on" : ""}`}
                  aria-pressed={on}
                  onClick={() => filters.set({ year_min: on ? null : from, page: null })}
                >
                  {label}
                </button>
              );
            })}
          </div>
          <RangeField
            label="سال ساخت (شمسی)"
            minKey="year_min"
            maxKey="year_max"
            placeholderMin="از ۱۳۸۰"
            placeholderMax="تا ۱۴۰۴"
          />
          <div className="preset-row">
            {MILEAGE_PRESETS.map(([label, max]) => {
              const on = String(max) === (mileageMax ?? "");
              return (
                <button
                  key={label}
                  type="button"
                  className={`preset${on ? " on" : ""}`}
                  aria-pressed={on}
                  onClick={() => filters.set({ mileage_max: on ? null : max, page: null })}
                >
                  {label}
                </button>
              );
            })}
          </div>
          <RangeField
            label="کارکرد (کیلومتر)"
            minKey="mileage_min"
            maxKey="mileage_max"
            placeholderMin="از"
            placeholderMax="تا"
          />
        </div>

        {showSpecs && (
          <div className="filter-group">
            <h3 className="card-title">مشخصات</h3>
            <Select
              id="transmission"
              label="گیربکس"
              anyLabel="همه"
              value={filters.get("transmission")}
              onChange={(v) => filters.set({ transmission: v, page: null })}
              options={TRANSMISSIONS.map((t) => ({ value: t, label: t }))}
            />
            <Select
              id="fuel"
              label="سوخت"
              anyLabel="همه"
              value={filters.get("fuel")}
              onChange={(v) => filters.set({ fuel: v, page: null })}
              options={FUELS.map((f) => ({ value: f, label: f }))}
            />
            <Select
              id="body_type"
              label="نوع بدنه"
              anyLabel="همه"
              value={filters.get("body_type")}
              onChange={(v) => filters.set({ body_type: v, page: null })}
              options={BODY_TYPES.map((b) => ({ value: b, label: b }))}
            />
            <Select
              id="condition"
              label="وضعیت بدنه"
              anyLabel="همه"
              value={filters.get("condition")}
              onChange={(v) => filters.set({ condition: v, page: null })}
              options={[
                { value: "clean", label: "بدون رنگ" },
                { value: "cosmetic", label: "لکه / خط و خش" },
                { value: "painted", label: "رنگ‌شده" },
                { value: "structural", label: "تعویض / تصادفی" },
              ]}
            />
          </div>
        )}

        {showConfidence && (
          <div className="filter-group">
            <h3 className="card-title">اعتبار محاسبه</h3>
            <Select
              id="confidence"
              label="بر پایه تعداد آگهی‌های مشابه"
              anyLabel="هر اعتباری"
              value={filters.get("confidence")}
              onChange={(v) => filters.set({ confidence: v, page: null })}
              options={[
                { value: "high", label: "زیاد (۴۰ آگهی مشابه و بیشتر)" },
                { value: "medium", label: "متوسط (۱۵ تا ۳۹)" },
                { value: "low", label: "کم (۸ تا ۱۴)" },
              ]}
            />
            <p className="empty-hint">
              هرچه آگهی‌های مشابه بیشتر باشند، قیمت میانه‌ای که این خودرو با آن
              سنجیده می‌شود قابل اعتمادتر است.
            </p>
          </div>
        )}
      </div>
      <ActiveChips />
    </section>
  );
}

/** Persian labels for whatever is currently narrowing the list. */
function chipLabel(key: string, value: string): string {
  switch (key) {
    case "q": return `جست‌وجو: ${value}`;
    case "brand": return `برند: ${value}`;
    case "model": return "مدل انتخاب‌شده";
    case "variant": return "تیپ انتخاب‌شده";
    case "price_min": return `از ${toman(Number(value))} تومان`;
    case "price_max": return `تا ${toman(Number(value))} تومان`;
    case "year_min": return `از سال ${value}`;
    case "year_max": return `تا سال ${value}`;
    case "mileage_min": return `کارکرد از ${Number(value).toLocaleString("en-US")}`;
    case "mileage_max": return `کارکرد زیر ${Number(value).toLocaleString("en-US")}`;
    case "condition":
      return `بدنه: ${{ clean: "بدون رنگ", cosmetic: "لکه/خط و خش", painted: "رنگ‌شده", structural: "تعویض/تصادفی" }[value] ?? value}`;
    case "transmission": return `گیربکس: ${value}`;
    case "fuel": return `سوخت: ${value}`;
    case "body_type": return `بدنه: ${value}`;
    case "seller_type": return value === "dealer" ? "نمایشگاه" : "فروشنده شخصی";
    case "confidence":
      return `اعتبار: ${{ high: "زیاد", medium: "متوسط", low: "کم" }[value] ?? value}`;
    default: return `${key}: ${value}`;
  }
}

function ActiveChips() {
  const filters = useFilters();
  const active = FILTER_KEYS.map((k) => [k, filters.get(k)] as const).filter(
    ([, v]) => v,
  );
  if (!active.length) return null;

  const clearAll = () => {
    const cleared: Record<string, null> = { page: null };
    for (const k of FILTER_KEYS) cleared[k] = null;
    filters.set(cleared);
  };

  return (
    <div className="chips">
      <Search size={13} aria-hidden />
      {active.map(([key, value]) => (
        <span key={key} className="chip">
          <Fa>{chipLabel(key, value as string)}</Fa>
          <button
            type="button"
            aria-label={`حذف فیلتر ${chipLabel(key, value as string)}`}
            onClick={() =>
              filters.set(
                // Dropping a model must drop the trim under it, or the list
                // filters on a trim belonging to a model nobody selected.
                key === "model"
                  ? { model: null, variant: null, page: null }
                  : { [key]: null, page: null },
              )
            }
          >
            <X size={12} />
          </button>
        </span>
      ))}
      <button className="ghost" onClick={clearAll}>پاک کردن همه</button>
    </div>
  );
}
