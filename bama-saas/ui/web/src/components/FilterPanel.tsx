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
import { useBrands, useCities } from "@/catalogue";
import { useIsFetching, useQuery } from "@tanstack/react-query";
import { Check, ChevronDown, ChevronsUpDown, Search, SlidersHorizontal, X } from "lucide-react";
import { api, type Variant } from "@/api";
import { useFilters } from "@/filters";
import { Fa, NumberInput } from "@/ui";
import { CONDITION_BANDS, conditionLabel, num, toman } from "@/format";
import { ModelCombobox, normalizePersianInput, useModelLabels } from "@/components/ModelCombobox";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList,
} from "@/components/ui/command";
import { cn } from "@/lib/utils";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";

const TRANSMISSIONS = ["اتوماتیک", "دنده ای"];
/** Bama's own colour names, most listed first. */
const COLORS = ["سفید", "مشکی", "خاکستری", "نقره ای", "نوک مدادی", "سفید صدفی", "آبی", "تیتانیوم", "سرمه ای", "قهوه ای", "بژ", "قرمز", "طوسی", "یشمی"];
const FUELS = ["بنزینی", "هیبریدی", "برقی", "دوگانه سوز", "پلاگین هیبرید", "بردافزا", "هیبرید ملایم", "دیزلی"];
const BODY_TYPES = ["سدان", "کراس اور", "هاچبک", "وانت", "شاسی بلند‌", "ون", "کوپه", "کروک"];


const CONFIDENCES = [
  { value: "high", label: "زیاد (40 آگهی مشابه و بیشتر)" },
  { value: "medium", label: "متوسط (15 تا 39)" },
  { value: "low", label: "کم (8 تا 14)" },
];

const BILLION = 1_000_000_000;
const PRICE_PRESETS: [string, number | null, number | null][] = [
  ["تا 500 میلیون", null, 500_000_000],
  ["500 تا 1 میلیارد", 500_000_000, BILLION],
  ["1 تا 2 میلیارد", BILLION, 2 * BILLION],
  ["2 تا 5 میلیارد", 2 * BILLION, 5 * BILLION],
  ["بالای 5 میلیارد", 5 * BILLION, null],
];

/**
 * Jalali, like every year in this app — `Ad.year` mixes 1399 and 2025.
 *
 * Derived from today rather than written down: the labels are relative ("the
 * last five years") but the values they set are absolute, so a hardcoded pair
 * silently becomes a lie one Nowruz later. The last preset is the odd one out
 * on purpose — it names its own year, so it can stay fixed.
 */
const THIS_JALALI_YEAR = Number(
  new Intl.DateTimeFormat("en-u-ca-persian", { year: "numeric" })
    .format(new Date())
    .replace(/\D/g, ""),
);

const YEAR_PRESETS: [string, number | null][] = [
  ["5 سال اخیر", THIS_JALALI_YEAR - 4],
  ["10 سال اخیر", THIS_JALALI_YEAR - 9],
  ["1390 به بعد", 1390],
];

/** Days back from today, kept relative in the URL so a shared link still means
 *  "this week" tomorrow. Explorer turns it into the API's `listed_from`. */
const LISTED_PRESETS: [string, number][] = [
  ["امروز", 1],
  ["3 روز اخیر", 3],
  ["هفته اخیر", 7],
  ["ماه اخیر", 30],
];

/** 0 km is its own group (its peers are other 0 km cars), so every "under"
 *  preset starts at 1 km. `[label, min, max]`. */
export const MILEAGE_PRESETS: [string, number, number | null][] = [
  ["صفر کیلومتر", 0, 0],
  ["زیر 5 هزار", 1, 5_000],
  ["زیر 10 هزار", 1, 10_000],
  ["زیر 20 هزار", 1, 20_000],
  ["زیر 50 هزار", 1, 50_000],
  ["زیر 100 هزار", 1, 100_000],
  ["بالای 100 هزار", 100_001, null],
];

/** A 0 km cap, however it was typed ("0", "00"). */
export const isZeroKm = (max: string | null | undefined) =>
  max != null && max.trim() !== "" && Number(max) === 0;

/** Mileage presets plus free from/to fields, shared by the Cars and Price tabs.
 *  Picking 0 km clears body condition: a 0 km car has no paint. */
export function MileageField() {
  const filters = useFilters();
  const min = filters.get("mileage_min") ?? "";
  const max = filters.get("mileage_max") ?? "";
  return (
    <>
      <PresetRow>
        {MILEAGE_PRESETS.map(([label, lo, hi]) => {
          const on = String(lo) === min && String(hi ?? "") === max;
          return (
            <Preset key={label} label={label} on={on}
                    onClick={() => filters.set(on
                      ? { mileage_min: null, mileage_max: null, page: null }
                      : { mileage_min: lo, mileage_max: hi, page: null,
                          ...(hi === 0 ? { condition: null } : {}) })} />
          );
        })}
      </PresetRow>
      <RangeField
        label="کارکرد (کیلومتر)"
        minKey="mileage_min" maxKey="mileage_max"
        placeholderMin="از" placeholderMax="تا"
      />
    </>
  );
}

export const FILTER_KEYS = [
  "brand", "model", "variant", "city", "q", "price_min", "price_max",
  "year_min", "year_max", "listed_days", "mileage_min", "mileage_max", "transmission", "fuel", "color",
  "body_type", "condition", "seller_type", "confidence",
];

/** Which controls a screen wants. Both lists apply every filter here; only
 *  the deal board has a confidence question. */
export interface FilterOptions {
  showConfidence?: boolean;
}

const ANY = "__any__";

/**
 * One filter group behind a button.
 *
 * The count on the trigger is what makes collapsing safe: a group can hide its
 * controls but never the fact that it is filtering.
 */
function Group({
  label, keys, count, children,
}: {
  label: string;
  keys: string[];
  /** Overrides the per-key count, for a group whose keys hold lists. */
  count?: number;
  children: React.ReactNode;
}) {
  const filters = useFilters();
  const active = count ?? keys.filter((k) => filters.get(k)).length;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant={active ? "secondary" : "outline"} size="sm" className="flex-none">
          {label}
          {active > 0 && (
            <span className="bg-primary text-primary-foreground grid size-4 place-items-center rounded-full text-[10px] font-bold">
              {active}
            </span>
          )}
          <ChevronDown className="size-3.5 opacity-60" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" collisionPadding={8} className="w-[min(20rem,90vw)] space-y-3">
        {children}
      </PopoverContent>
    </Popover>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="grid grid-cols-1 gap-1.5">
      <span className="text-muted-foreground text-xs font-semibold">{label}</span>
      {children}
    </label>
  );
}

/** A labelled min/max pair. Committed on blur or Enter, like the rest of the
 *  app — filtering per keystroke would refetch on every digit of a price. A
 *  pair typed backwards is swapped rather than silently matching nothing. */
/** 1500000000 → "1,500,000,000": a ten-digit number is unreadable without commas. */
const grouped = (v?: string) => (v && /^\d+$/.test(v) ? Number(v).toLocaleString("en-US") : (v ?? ""));

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
  const commit = (key: string, raw: string) => {
    const next: Record<string, string | null> = { [minKey]: min ?? null, [maxKey]: max ?? null };
    next[key] = raw || null;
    const lo = next[minKey], hi = next[maxKey];
    if (lo != null && hi != null && Number(lo) > Number(hi)) {
      next[minKey] = hi;
      next[maxKey] = lo;
    }
    filters.set({
      ...next, page: null,
      // A typed 0 km max clears body condition, like the 0 km preset: the
      // condition control hides at 0 km and would otherwise be stuck on.
      ...(maxKey === "mileage_max" && isZeroKm(next[maxKey]) ? { condition: null } : {}),
    });
  };
  const onEnter = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") e.currentTarget.blur();
  };
  return (
    <Field label={label}>
      <div className="flex items-center gap-2">
        <NumberInput
          key={`${minKey}-${min}`}
          className={input}
          aria-label={`${label} از`}
          placeholder={placeholderMin}
          defaultValue={grouped(min)}
          onKeyDown={onEnter}
          onBlur={(e) => commit(minKey, e.target.value)}
        />
        <span className="text-muted-foreground flex-none text-xs">تا</span>
        <NumberInput
          key={`${maxKey}-${max}`}
          className={input}
          aria-label={`${label} تا`}
          placeholder={placeholderMax}
          defaultValue={grouped(max)}
          onKeyDown={onEnter}
          onBlur={(e) => commit(maxKey, e.target.value)}
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

/**
 * 328 cities is too many for a plain select: it needs typing, and it needs to
 * say it is still loading rather than open on «همه شهرها» alone.
 */
function CityPicker() {
  const filters = useFilters();
  const cities = useCities();
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const value = filters.get("city");
  const selected = cities.data?.find((c) => String(c.id) === value);
  const needle = normalizePersianInput(term);
  const shown = (cities.data ?? []).filter((c) => !needle || c.name_fa.includes(needle));
  const pick = (city: string | null) => {
    filters.set({ city, page: null });
    setTerm("");
    setOpen(false);
  };

  return (
    <Field label="شهر">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline" role="combobox" aria-expanded={open}
            aria-label={selected?.name_fa ?? "همه شهرها"}
            className="w-full justify-between font-normal"
          >
            <span className="truncate">
              {selected ? <Fa>{selected.name_fa}</Fa> : <span className="text-muted-foreground">همه شهرها</span>}
            </span>
            <ChevronsUpDown className="size-4 flex-none opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" collisionPadding={8} className="w-[min(18rem,90vw)] p-0">
          <Command shouldFilter={false}>
            <CommandInput placeholder="جست‌وجوی شهر…" value={term} onValueChange={setTerm} />
            <CommandList>
              {cities.isLoading ? (
                <div className="text-muted-foreground p-3 text-sm">در حال دریافت شهرها…</div>
              ) : cities.isError ? (
                <div role="alert" className="p-3 text-sm">دریافت شهرها انجام نشد.</div>
              ) : (
                <CommandEmpty>شهری پیدا نشد</CommandEmpty>
              )}
              <CommandGroup>
                {value && <CommandItem value="__all__" onSelect={() => pick(null)}>همه شهرها</CommandItem>}
                {shown.map((c) => (
                  <CommandItem key={c.id} value={String(c.id)} onSelect={() => pick(String(c.id))}>
                    <Check className={cn("size-4", String(c.id) === value ? "opacity-100" : "opacity-0")} />
                    <span className="truncate"><Fa>{c.name_fa}</Fa></span>
                    <span className="text-muted-foreground ms-auto font-mono text-[11.5px]">{num(c.ad_count)}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
    </Field>
  );
}

/** 147 brands: a plain select cannot be searched, so this is the city picker's twin. */
function BrandPicker({
  options, onPick,
}: {
  options: { value: string; label: string; count: number }[];
  onPick: (slug: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const needle = normalizePersianInput(term);
  const shown = options.filter((o) => !needle || o.label.includes(needle));
  return (
    <Field label="یا همه‌ی مدل‌های یک برند">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button variant="outline" role="combobox" aria-expanded={open} className="w-full justify-between font-normal">
            <span className="text-muted-foreground">انتخاب برند</span>
            <ChevronsUpDown className="size-4 flex-none opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" collisionPadding={8} className="w-[min(18rem,90vw)] p-0">
          <Command shouldFilter={false}>
            <CommandInput placeholder="جست‌وجوی برند…" value={term} onValueChange={setTerm} />
            <CommandList>
              <CommandEmpty>برندی پیدا نشد</CommandEmpty>
              <CommandGroup>
                {shown.map((o) => (
                  <CommandItem
                    key={o.value} value={o.value}
                    onSelect={() => { onPick(o.value); setTerm(""); setOpen(false); }}
                  >
                    <span className="truncate"><Fa>{o.label}</Fa></span>
                    <span className="text-muted-foreground ms-auto font-mono text-[11.5px]">{num(o.count)}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
    </Field>
  );
}

/** Comma-separated URL list, e.g. `model=12,40`. */
export const listParam = (raw?: string) => (raw ? raw.split(",").filter(Boolean) : []);
const MAX_CARS = 10;

/**
 * Several cars in one list: whole brands and single models, mixed.
 *
 * A picked model is stored in `model`, a whole brand in `brand`. The API reads
 * a brand that also has a picked model as narrowed by it, so the old one-car
 * links (`brand=X&model=Y`) still mean model Y.
 */
function CarPicker() {
  const filters = useFilters();
  const { list: brandList } = useBrands();
  const brands = listParam(filters.get("brand"));
  const models = listParam(filters.get("model"));
  const labels = useModelLabels(models);
  // Until the picked models' brands are known, a brand pick cannot drop them,
  // and a kept model narrows the brand straight back (`car_scope_q`). A shared
  // link on a slow first load hit exactly that.
  const labelsLoading = useIsFetching({ queryKey: ["models-by-id", models.join(",")] }) > 0;
  const full = brands.length + models.length >= MAX_CARS;
  const set = (nextBrands: string[], nextModels: string[]) =>
    filters.set({
      brand: nextBrands.join(",") || null,
      model: nextModels.join(",") || null,
      variant: null,
      page: null,
    });
  return (
    <div className="space-y-3">
      <CarChips />
      {full ? (
        <p className="text-muted-foreground text-xs">حداکثر {num(MAX_CARS)} خودرو.</p>
      ) : (
        <>
          <Field label="افزودن مدل">
            <ModelCombobox
              placeholder="نام مدل را بنویسید…"
              onSelect={(picked) => {
                if (picked && !models.includes(String(picked.id))) {
                  set(brands, [...models, String(picked.id)]);
                }
              }}
              onSelectMany={(picked) => {
                const room = MAX_CARS - brands.length - models.length;
                const fresh = picked.map((m) => String(m.id)).filter((id) => !models.includes(id));
                set(brands, [...models, ...fresh.slice(0, Math.max(room, 0))]);
              }}
            />
          </Field>
          {!labelsLoading && <BrandPicker
            // "All of a brand" replaces that brand's single models. Kept beside
            // them, the models narrowed the brand back down and the pick did
            // nothing (`car_scope_q`).
            onPick={(v) => set([...brands.filter((b) => b !== v), v],
                               models.filter((id) => labels.find((m) => String(m.id) === id)?.brand_slug !== v))}
            options={brandList
              .filter((b) => !brands.includes(b.slug) || labels.some((m) => m.brand_slug === b.slug))
              .map((b) => ({ value: b.slug, label: b.name_fa, count: b.ad_count }))}
          />}
        </>
      )}
    </div>
  );
}

/** One removable chip per picked car. Shared by the picker and the chip row. */
function CarChips({ inline = false }: { inline?: boolean }) {
  const items = useCarItems();
  if (!items.length) return null;
  return (
    <div className={inline ? "contents" : "chips"}>
      {items.map((item) => (
        <span key={item.key} className="chip">
          <Fa>{item.label}</Fa>
          <button type="button" aria-label={`حذف ${item.label}`} onClick={item.drop}>
            <X size={12} />
          </button>
        </span>
      ))}
    </div>
  );
}

/** The picked cars as removable items: whole brands, then single models. */
function useCarItems() {
  const filters = useFilters();
  const { list: brandList } = useBrands();
  const brands = listParam(filters.get("brand"));
  const models = listParam(filters.get("model"));
  const labels = useModelLabels(models);
  // A brand narrowed by one of its own picked models is not a separate car.
  const narrowed = new Set(labels.map((m) => m.brand_slug));
  const remove = (nextBrands: string[], nextModels: string[]) =>
    filters.set({
      brand: nextBrands.join(",") || null,
      model: nextModels.join(",") || null,
      variant: null,
      page: null,
    });
  const items = [
    ...brands.filter((b) => !narrowed.has(b)).map((slug) => ({
      key: `b-${slug}`,
      label: `همه‌ی ${brandList.find((b) => b.slug === slug)?.name_fa ?? slug}`,
      drop: () => remove(brands.filter((b) => b !== slug), models),
    })),
    ...models.map((id) => {
      const m = labels.find((x) => String(x.id) === id);
      return {
        key: `m-${id}`,
        label: m ? `${m.brand_name} ${m.name_fa}` : "…",
        drop: () => remove(
          // Dropping the last picked model of a narrowed brand drops the brand too,
          // or the list would silently widen to the whole brand.
          brands.filter((b) => !(m && b === m.brand_slug &&
            !labels.some((o) => o.brand_slug === b && String(o.id) !== id))),
          models.filter((x) => x !== id),
        ),
      };
    }),
  ];
  return items;
}

function TrimPicker() {
  const filters = useFilters();
  const picked = listParam(filters.get("model"));
  const brands = listParam(filters.get("brand"));
  const [label] = useModelLabels(picked);
  // A trim belongs to one model. With any other car picked beside it, a trim
  // filter would silently drop every row of the others.
  const model = picked.length === 1 && label && brands.every((b) => b === label.brand_slug)
    ? picked[0] : undefined;
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

export function FilterPanel({ showConfidence = false }: FilterOptions) {
  const filters = useFilters();
  const carCount = useCarItems().length;

  const priceMin = filters.get("price_min");
  const priceMax = filters.get("price_max");
  const yearMin = filters.get("year_min");
  const mileageMax = filters.get("mileage_max");
  const listedDays = filters.get("listed_days");

  return (
    <section
      aria-label="فیلترها"
      className="border-border bg-panel mb-4 rounded-[var(--radius)] border p-3 shadow-sm"
    >
      {/* On a phone: search on its own full-width row, then the filter
          buttons as one horizontally scrolling row. The search used to share a
          wrapping row with them and was squeezed to a 20px sliver — the box
          was on screen and could not be typed into. From `sm` up it is one
          wrapping toolbar, as before. */}
      <div className="flex flex-wrap items-center gap-2">
        <SlidersHorizontal className="text-muted-foreground hidden size-4 flex-none sm:block" aria-hidden />

        <SearchBox key={filters.get("q") ?? ""} />

        <div className="scroll-hint flex min-w-0 basis-full items-center gap-2 overflow-x-auto pb-0.5 [scrollbar-width:none] sm:mx-0 sm:basis-auto sm:flex-wrap sm:overflow-visible sm:px-0 sm:pb-0">
        <Group label="خودرو" keys={["brand", "model", "variant"]} count={carCount}>
          <CarPicker />
          <TrimPicker />
        </Group>

        <Group label="شهر" keys={["city"]}>
          <CityPicker />
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

        <Group
          label="سال و کارکرد"
          keys={["year_min", "year_max", "mileage_min", "mileage_max"]}
          count={[yearMin, filters.get("year_max")].filter(Boolean).length +
                 (mileageLabel(filters.get("mileage_min"), mileageMax) ? 1 : 0)}
        >
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
            label="سال ساخت (شمسی یا میلادی)"
            minKey="year_min" maxKey="year_max"
            placeholderMin="از 1395 یا 2016" placeholderMax="تا 1404 یا 2025"
          />
          <MileageField />
        </Group>

        <Group label="تاریخ ثبت" keys={["listed_days"]}>
          <PresetRow>
            {LISTED_PRESETS.map(([label, days]) => {
              const on = String(days) === (listedDays ?? "");
              return (
                <Preset key={label} label={label} on={on}
                        onClick={() => filters.set({ listed_days: on ? null : days, page: null })} />
              );
            })}
          </PresetRow>
        </Group>

        <Group label="مشخصات" keys={["transmission", "fuel", "color", "body_type", "condition"]}>
          <Choice
            label="گیربکس" anyLabel="همه"
            value={filters.get("transmission")}
            onChange={(v) => filters.set({ transmission: v, page: null })}
            options={TRANSMISSIONS.map((t) => ({ value: t, label: t }))}
          />
          <Choice
            label="رنگ" anyLabel="همه"
            value={filters.get("color")}
            onChange={(v) => filters.set({ color: v, page: null })}
            options={COLORS.map((c) => ({ value: c, label: c }))}
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
          {!isZeroKm(mileageMax) && <Choice
            label="وضعیت بدنه" anyLabel="همه"
            value={filters.get("condition")}
            onChange={(v) => filters.set({ condition: v, page: null })}
            options={[...CONDITION_BANDS]}
          />}
        </Group>

        {showConfidence && (
          <Group label="میزان اطمینان قیمت" keys={["confidence"]}>
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
      </div>

      <ActiveChips showConfidence={showConfidence} />
    </section>
  );
}

/**
 * Free-text search. Committed on blur or Enter, never per keystroke.
 *
 * A real `<form>` with a real submit button. Enter and blur both worked before
 * and neither was discoverable: typing changed nothing on screen, there was no
 * control to press, and nothing said which key would do it — so the box read as
 * broken. The button is the affordance; Enter still works because a form does
 * that for free.
 */
function SearchBox() {
  const filters = useFilters();
  const current = filters.get("q");
  const [draft, setDraft] = useState(current ?? "");

  const commit = () => filters.set({ q: draft.trim() || null, page: 1 });
  return (
    <form
      className="flex min-w-0 basis-full items-center gap-1 sm:basis-auto sm:flex-1 sm:max-w-72"
      onSubmit={(e) => { e.preventDefault(); commit(); }}
    >
      <input
        key={current}
        type="search"
        aria-label="جست‌وجو در آگهی‌ها"
        placeholder="عنوان، برند یا توضیحات"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        className="border-border bg-panel h-8 min-w-0 flex-1 rounded-md border px-3 text-sm"
      />
      <Button type="submit" size="sm" variant="outline" aria-label="جست‌وجو">
        <Search className="size-4" />
      </Button>
    </form>
  );
}

/** Persian labels for whatever is currently narrowing the list. */
function chipLabel(key: string, value: string): string {
  switch (key) {
    case "q": return `جست‌وجو: ${value}`;
    case "variant": return "تیپ انتخاب‌شده";
    case "city": return "شهر انتخاب‌شده";
    case "price_min": return `از ${toman(Number(value))} تومان`;
    case "price_max": return `تا ${toman(Number(value))} تومان`;
    case "year_min": return `از سال ${value}`;
    case "year_max": return `تا سال ${value}`;
    case "listed_days": return `ثبت در ${num(Number(value))} روز اخیر`;
    case "condition":
      return `بدنه: ${conditionLabel(value)}`;
    case "transmission": return `گیربکس: ${value}`;
    case "fuel": return `سوخت: ${value}`;
    case "color": return `رنگ: ${value}`;
    case "body_type": return `بدنه: ${value}`;
    case "seller_type": return value === "dealer" ? "نمایشگاه" : "فروشنده شخصی";
    case "confidence":
      return `اعتبار: ${{ high: "زیاد", medium: "متوسط", low: "کم" }[value] ?? value}`;
    default: return `${key}: ${value}`;
  }
}

/** The mileage pair as one phrase; presets start "under" at 1 km. */
export function mileageLabel(min?: string, max?: string): string | null {
  const lo = min ? Number(min) : 0;
  if (!max) return lo > 0 ? `کارکرد بالای ${num(lo)}` : null;
  const hi = Number(max);
  if (hi === 0) return "صفر کیلومتر";
  return lo <= 1 ? `کارکرد زیر ${num(hi)}` : `کارکرد ${num(lo)} تا ${num(hi)}`;
}

/** Every active filter, individually removable. A single "clear all" made
 *  dropping one of five choices mean re-entering the other four. */
function ActiveChips({ showConfidence }: { showConfidence: boolean }) {
  const filters = useFilters();
  const cities = useCities();
  // A chip for a filter this screen does not apply would claim a narrowing
  // that is not happening.
  const all = FILTER_KEYS.filter((k) => showConfidence || k !== "confidence")
    .map((k) => [k, filters.get(k)] as const).filter(([, v]) => v);
  if (!all.length) return null;
  // Picked cars get one chip each from `CarChips`; mileage is one chip for
  // its pair, so 0 km is «صفر کیلومتر» and not "from 0" plus "under 0".
  const active = all.filter(([k]) =>
    k !== "brand" && k !== "model" && k !== "mileage_min" && k !== "mileage_max");
  const mileage = mileageLabel(filters.get("mileage_min"), filters.get("mileage_max"));

  const clearAll = () => {
    const cleared: Record<string, null> = { page: null };
    for (const k of FILTER_KEYS) cleared[k] = null;
    filters.set(cleared);
  };

  return (
    <div className="chips mt-3">
      <CarChips inline />
      {active.map(([key, value]) => {
        const label = key === "city"
          ? `شهر: ${cities.data?.find((c) => String(c.id) === value)?.name_fa ?? value}`
          : chipLabel(key, value as string);
        return (
          <span key={key} className="chip">
            <Fa>{label}</Fa>
            <button
              type="button"
              aria-label={`حذف فیلتر ${label}`}
              onClick={() =>
                filters.set(
                  { [key]: null, page: null },
                )
              }
            >
              <X size={12} />
            </button>
          </span>
        );
      })}
      {mileage && (
        <span className="chip">
          <Fa>{mileage}</Fa>
          <button
            type="button"
            aria-label={`حذف فیلتر ${mileage}`}
            onClick={() => filters.set({ mileage_min: null, mileage_max: null, page: null })}
          >
            <X size={12} />
          </button>
        </span>
      )}
      <Button variant="ghost" size="sm" onClick={clearAll}>پاک کردن همه</Button>
    </div>
  );
}
