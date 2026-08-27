/**
 * The filter toolbar, shared by the deal board and the explorer.
 *
 * It used to be a permanently-expanded wall of bare `<select>` and number
 * inputs sitting on top of both screens — five groups printed in full whether or
 * not anyone was using them. Now each group is one button that opens a popover,
 * and the button says how many filters are set inside it, so collapsing a group
 * never hides the fact that it is narrowing the list.
 *
 * The active-filter chips stay permanently visible below the buttons. They are
 * the answer to "why am I seeing these results", and each removes one filter
 * without re-entering the other four.
 *
 * State still lives in the URL via `useFilters`, so every view stays shareable
 * and the back button keeps working. Nothing here holds a second copy.
 *
 * The scraped vocabularies below must stay byte-equal to the text stored on
 * `Ad`: the backend matches `transmission` case-sensitively.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, SlidersHorizontal, X } from "lucide-react";
import { api } from "./api";
import type { Paginated } from "./api";
import { useFilters } from "./filters";
import { Fa, toman } from "./ui";
import { ModelCombobox, useModelLabel } from "./components/ModelCombobox";
import { Button } from "./components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "./components/ui/popover";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "./components/ui/select";

const TRANSMISSIONS = ["اتوماتیک", "دنده ای"];
const FUELS = ["بنزینی", "هیبریدی", "برقی", "دوگانه سوز", "پلاگین هیبرید", "بردافزا", "هیبرید ملایم", "دیزلی"];
const BODY_TYPES = ["سدان", "کراس اور", "هاچبک", "وانت", "شاسی بلند‌", "ون", "کوپه", "کروک"];

const CONDITIONS = [
  { value: "clean", label: "بدون رنگ" },
  { value: "cosmetic", label: "لکه / خط و خش" },
  { value: "painted", label: "رنگ‌شده" },
  { value: "structural", label: "تعویض / تصادفی" },
];

const CONFIDENCES = [
  { value: "high", label: "زیاد (۴۰ آگهی مشابه و بیشتر)" },
  { value: "medium", label: "متوسط (۱۵ تا ۳۹)" },
  { value: "low", label: "کم (۸ تا ۱۴)" },
];

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
interface Variant { id: number; name_fa: string }

/** Which controls a screen wants. The board has no body-type question; the
 *  explorer has no confidence one. */
export interface FilterOptions {
  showSpecs?: boolean;
  showConfidence?: boolean;
  showSearch?: boolean;
}

const ANY = "__any__";

/**
 * One filter group behind a button.
 *
 * The count on the trigger is what makes collapsing safe: a group can hide its
 * controls but never the fact that it is filtering.
 */
function Group({
  label, keys, children,
}: {
  label: string;
  keys: string[];
  children: React.ReactNode;
}) {
  const filters = useFilters();
  const active = keys.filter((k) => filters.get(k)).length;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant={active ? "secondary" : "outline"} size="sm">
          {label}
          {active > 0 && (
            <span className="bg-primary text-primary-foreground grid size-4 place-items-center rounded-full text-[10px] font-bold">
              {active}
            </span>
          )}
          <ChevronDown className="size-3.5 opacity-60" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(20rem,90vw)] space-y-3">
        {children}
      </PopoverContent>
    </Popover>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="grid gap-1.5">
      <span className="text-muted-foreground text-xs font-semibold">{label}</span>
      {children}
    </label>
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
  const input =
    "border-border bg-panel min-w-0 flex-1 rounded-md border px-2.5 py-1.5 text-sm";
  return (
    <Field label={label}>
      <div className="flex items-center gap-2">
        <input
          key={`${minKey}-${min}`}
          className={input}
          type="number"
          inputMode="numeric"
          aria-label={`${label} از`}
          placeholder={placeholderMin}
          defaultValue={min ?? ""}
          onBlur={(e) => filters.set({ [minKey]: e.target.value || null, page: null })}
        />
        <span className="text-muted-foreground flex-none text-xs">تا</span>
        <input
          key={`${maxKey}-${max}`}
          className={input}
          type="number"
          inputMode="numeric"
          aria-label={`${label} تا`}
          placeholder={placeholderMax}
          defaultValue={max ?? ""}
          onBlur={(e) => filters.set({ [maxKey]: e.target.value || null, page: null })}
        />
      </div>
    </Field>
  );
}

function PresetRow({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-wrap gap-1.5">{children}</div>;
}

function Preset({
  label, on, onClick,
}: { label: string; on: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      className={`preset${on ? " on" : ""}`}
      aria-pressed={on}
      onClick={onClick}
    >
      {label}
    </button>
  );
}

function Choice({
  label, value, anyLabel, options, onChange,
}: {
  label: string;
  value?: string;
  anyLabel: string;
  options: { value: string; label: string }[];
  onChange: (v: string | null) => void;
}) {
  return (
    <Field label={label}>
      <Select
        value={value ?? ANY}
        onValueChange={(next) => onChange(next === ANY ? null : next)}
      >
        <SelectTrigger><SelectValue placeholder={anyLabel} /></SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>{anyLabel}</SelectItem>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}><Fa>{o.label}</Fa></SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Field>
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
    <Choice
      label="تیپ"
      anyLabel="همه تیپ‌ها"
      value={filters.get("variant")}
      onChange={(v) => filters.set({ variant: v, page: null })}
      options={variants.data.map((v) => ({ value: String(v.id), label: v.name_fa }))}
    />
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
    staleTime: 10 * 60_000,
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
    <section
      aria-label="فیلترها"
      className="border-border bg-panel mb-4 rounded-[var(--radius)] border p-3 shadow-sm"
    >
      <div className="flex flex-wrap items-center gap-2">
        <SlidersHorizontal className="text-muted-foreground size-4 flex-none" aria-hidden />

        {showSearch && <SearchBox />}

        <Group label="خودرو" keys={["brand", "model", "variant"]}>
          <Choice
            label="برند"
            anyLabel="همه برندها"
            value={brand}
            onChange={(v) => filters.set({ brand: v, model: null, variant: null, page: null })}
            options={brandList.map((b) => ({ value: b.slug, label: b.name_fa }))}
          />
          <Field label="مدل خودرو">
            <ModelCombobox
              value={filters.get("model")}
              brand={brand}
              placeholder="همه مدل‌ها"
              onSelect={(picked) =>
                filters.set(
                  picked
                    // Setting the brand too keeps the coarse select honest: it
                    // would otherwise still read "همه برندها" next to one model.
                    ? { model: picked.id, brand: picked.brand_slug, variant: null, page: null }
                    : { model: null, variant: null, page: null },
                )
              }
            />
          </Field>
          <TrimPicker />
        </Group>

        <Group label="قیمت" keys={["price_min", "price_max"]}>
          <PresetRow>
            {PRICE_PRESETS.map(([label, lo, hi]) => {
              const on = String(lo ?? "") === (priceMin ?? "") &&
                         String(hi ?? "") === (priceMax ?? "");
              return (
                <Preset
                  key={label}
                  label={label}
                  on={on}
                  onClick={() => filters.set({
                    price_min: on ? null : lo, price_max: on ? null : hi, page: null,
                  })}
                />
              );
            })}
          </PresetRow>
          {/* Presets sit above the free-entry pair rather than replacing it:
              most people want a round number, some want an exact one, and
              hiding either costs more than showing both. */}
          <RangeField
            label="یا بازه دلخواه (تومان)"
            minKey="price_min" maxKey="price_max"
            placeholderMin="از" placeholderMax="تا"
          />
        </Group>

        <Group label="سال و کارکرد" keys={["year_min", "year_max", "mileage_min", "mileage_max"]}>
          <PresetRow>
            {YEAR_PRESETS.map(([label, from]) => {
              const on = String(from ?? "") === (yearMin ?? "");
              return (
                <Preset key={label} label={label} on={on}
                        onClick={() => filters.set({ year_min: on ? null : from, page: null })} />
              );
            })}
          </PresetRow>
          <RangeField
            label="سال ساخت (شمسی)"
            minKey="year_min" maxKey="year_max"
            placeholderMin="از ۱۳۸۰" placeholderMax="تا ۱۴۰۴"
          />
          <PresetRow>
            {MILEAGE_PRESETS.map(([label, max]) => {
              const on = String(max) === (mileageMax ?? "");
              return (
                <Preset key={label} label={label} on={on}
                        onClick={() => filters.set({ mileage_max: on ? null : max, page: null })} />
              );
            })}
          </PresetRow>
          <RangeField
            label="کارکرد (کیلومتر)"
            minKey="mileage_min" maxKey="mileage_max"
            placeholderMin="از" placeholderMax="تا"
          />
        </Group>

        {showSpecs && (
          <Group label="مشخصات" keys={["transmission", "fuel", "body_type", "condition"]}>
            <Choice
              label="گیربکس" anyLabel="همه"
              value={filters.get("transmission")}
              onChange={(v) => filters.set({ transmission: v, page: null })}
              options={TRANSMISSIONS.map((t) => ({ value: t, label: t }))}
            />
            <Choice
              label="سوخت" anyLabel="همه"
              value={filters.get("fuel")}
              onChange={(v) => filters.set({ fuel: v, page: null })}
              options={FUELS.map((f) => ({ value: f, label: f }))}
            />
            <Choice
              label="نوع بدنه" anyLabel="همه"
              value={filters.get("body_type")}
              onChange={(v) => filters.set({ body_type: v, page: null })}
              options={BODY_TYPES.map((b) => ({ value: b, label: b }))}
            />
            <Choice
              label="وضعیت بدنه" anyLabel="همه"
              value={filters.get("condition")}
              onChange={(v) => filters.set({ condition: v, page: null })}
              options={CONDITIONS}
            />
          </Group>
        )}

        {showConfidence && (
          <Group label="اعتبار محاسبه" keys={["confidence"]}>
            <Choice
              label="بر پایه تعداد آگهی‌های مشابه"
              anyLabel="هر اعتباری"
              value={filters.get("confidence")}
              onChange={(v) => filters.set({ confidence: v, page: null })}
              options={CONFIDENCES}
            />
            <p className="text-muted-foreground text-xs">
              هرچه آگهی‌های مشابه بیشتر باشند، قیمت میانه‌ای که این خودرو با آن سنجیده
              می‌شود قابل اعتمادتر است.
            </p>
          </Group>
        )}
      </div>

      <ActiveChips />
    </section>
  );
}

/** Free-text search. Committed on blur or Enter, never per keystroke. */
function SearchBox() {
  const filters = useFilters();
  const current = filters.get("q");
  const [draft, setDraft] = useState(current ?? "");

  const commit = () => filters.set({ q: draft.trim() || null, page: 1 });
  return (
    <input
      key={current}
      type="search"
      aria-label="جست‌وجو در آگهی‌ها"
      placeholder="عنوان، برند یا توضیحات"
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && commit()}
      className="border-border bg-panel h-8 min-w-0 flex-1 rounded-md border px-3 text-sm sm:max-w-64"
    />
  );
}

/** Persian labels for whatever is currently narrowing the list. */
function chipLabel(key: string, value: string, modelName?: string): string {
  switch (key) {
    case "q": return `جست‌وجو: ${value}`;
    case "brand": return `برند: ${value}`;
    case "model": return modelName ? `مدل: ${modelName}` : "مدل انتخاب‌شده";
    case "variant": return "تیپ انتخاب‌شده";
    case "price_min": return `از ${toman(Number(value))} تومان`;
    case "price_max": return `تا ${toman(Number(value))} تومان`;
    case "year_min": return `از سال ${value}`;
    case "year_max": return `تا سال ${value}`;
    case "mileage_min": return `کارکرد از ${Number(value).toLocaleString("en-US")}`;
    case "mileage_max": return `کارکرد زیر ${Number(value).toLocaleString("en-US")}`;
    case "condition":
      return `بدنه: ${CONDITIONS.find((c) => c.value === value)?.label ?? value}`;
    case "transmission": return `گیربکس: ${value}`;
    case "fuel": return `سوخت: ${value}`;
    case "body_type": return `بدنه: ${value}`;
    case "seller_type": return value === "dealer" ? "نمایشگاه" : "فروشنده شخصی";
    case "confidence":
      return `اعتبار: ${{ high: "زیاد", medium: "متوسط", low: "کم" }[value] ?? value}`;
    default: return `${key}: ${value}`;
  }
}

/** Every active filter, individually removable. A single "clear all" made
 *  dropping one of five choices mean re-entering the other four. */
function ActiveChips() {
  const filters = useFilters();
  // The chip used to read "مدل انتخاب‌شده" because the panel never knew which
  // model; the picker resolves it by id now, so the chip can say the name.
  const selectedModel = useModelLabel(filters.get("model"));
  const active = FILTER_KEYS.map((k) => [k, filters.get(k)] as const).filter(([, v]) => v);
  if (!active.length) return null;

  const modelName = selectedModel
    ? `${selectedModel.brand_name} ${selectedModel.name_fa}`
    : undefined;

  const clearAll = () => {
    const cleared: Record<string, null> = { page: null };
    for (const k of FILTER_KEYS) cleared[k] = null;
    filters.set(cleared);
  };

  return (
    <div className="chips mt-3">
      {active.map(([key, value]) => {
        const label = chipLabel(key, value as string, modelName);
        return (
          <span key={key} className="chip">
            <Fa>{label}</Fa>
            <button
              type="button"
              aria-label={`حذف فیلتر ${label}`}
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
        );
      })}
      <Button variant="ghost" size="sm" onClick={clearAll}>پاک کردن همه</Button>
    </div>
  );
}
