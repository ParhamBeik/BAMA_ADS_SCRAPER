/**
 * What the analysis is about: the whole market, a brand, a model, a trim, or a
 * single model year.
 *
 * The scope lives in the URL (`?brand=&model=&variant=&year=`) so an analysis is
 * shareable and the back button walks back out of it. Each level clears the ones
 * below it — a trim belonging to a model nobody selected would silently filter
 * every panel on the page.
 *
 * The year list is not a range of plausible numbers: it comes from the
 * distribution response, which reports exactly the years this scope has data
 * for. Choosing one therefore cannot land on an empty page.
 *
 * Years are Jalali throughout, like every year in this app — `Ad.year` mixes
 * 1399 and 2025 in one column and is provenance, never a key.
 */
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api";
import type { Paginated } from "@/api";
import { useFilters } from "@/filters";
import { Fa } from "@/ui";
import { ModelCombobox, useModelLabel } from "@/components/ModelCombobox";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";

interface Brand { slug: string; name_fa: string }
interface Variant { id: number; name_fa: string }

const ANY = "__any__";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="grid min-w-0 gap-1.5">
      <span className="text-muted-foreground text-xs font-semibold">{label}</span>
      {children}
    </label>
  );
}

export function ScopePicker({ years }: { years?: { year_jalali: number; n: number }[] }) {
  const filters = useFilters();
  const brand = filters.get("brand");
  const model = filters.get("model");
  const variant = filters.get("variant");
  const year = filters.get("year");

  const brands = useQuery({
    queryKey: ["brands"],
    staleTime: 10 * 60_000,
    queryFn: ({ signal }) => api.get<Paginated<Brand> | Brand[]>("/api/brands/", signal),
  });
  const brandList: Brand[] = Array.isArray(brands.data)
    ? brands.data
    : (brands.data?.results ?? []);

  const variants = useQuery({
    queryKey: ["variants", model],
    enabled: Boolean(model),
    queryFn: ({ signal }) => api.get<Variant[]>(`/api/models/${model}/variants/`, signal),
  });

  // A link from elsewhere in the app carries only `?model=`, which leaves the
  // brand select reading "همه برندها" beside one named model. Fill the brand in
  // rather than merely displaying it: a select whose shown value is not its
  // state is a select that ignores a click on that value, and the model list
  // reads `brand` too, so displaying alone would also leave it unnarrowed.
  // `filters.set` replaces rather than pushes, so this does not cost a history
  // entry: it is the same scope written properly, not a step back.
  const modelLabel = useModelLabel(model ?? undefined);
  const impliedBrand = modelLabel?.brand_slug;
  const setFilters = filters.set;
  useEffect(() => {
    if (model && !brand && impliedBrand) setFilters({ brand: impliedBrand });
  }, [model, brand, impliedBrand, setFilters]);

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Field label="برند">
        <Select
          value={brand ?? ANY}
          onValueChange={(next) =>
            filters.set({
              brand: next === ANY ? null : next,
              model: null, variant: null, year: null,
            })
          }
        >
          <SelectTrigger><SelectValue placeholder="همه برندها" /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>همه برندها</SelectItem>
            {brandList.map((b) => (
              <SelectItem key={b.slug} value={b.slug}><Fa>{b.name_fa}</Fa></SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>

      <Field label="مدل">
        <ModelCombobox
          value={model}
          brand={brand}
          placeholder="همه مدل‌ها"
          onSelect={(picked) =>
            filters.set(
              picked
                // Setting the brand too keeps the coarse select honest: it would
                // otherwise still read "همه برندها" next to one model.
                ? { model: picked.id, brand: picked.brand_slug, variant: null, year: null }
                : { model: null, variant: null, year: null },
            )
          }
        />
      </Field>

      <Field label="تیپ">
        <Select
          value={variant ?? ANY}
          disabled={!model || !variants.data?.length}
          onValueChange={(next) =>
            filters.set({ variant: next === ANY ? null : next })
          }
        >
          <SelectTrigger>
            <SelectValue placeholder={model ? "همه تیپ‌ها" : "اول مدل را انتخاب کنید"} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>همه تیپ‌ها</SelectItem>
            {variants.data?.map((v) => (
              <SelectItem key={v.id} value={String(v.id)}><Fa>{v.name_fa}</Fa></SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>

      <Field label="سال ساخت (شمسی)">
        <Select
          value={year ?? ANY}
          disabled={!years?.length}
          onValueChange={(next) => filters.set({ year: next === ANY ? null : next })}
        >
          <SelectTrigger>
            <SelectValue placeholder={years?.length ? "همه سال‌ها" : "—"} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>همه سال‌ها</SelectItem>
            {years?.map((y) => (
              <SelectItem key={y.year_jalali} value={String(y.year_jalali)}>
                {y.year_jalali}
                <span className="text-muted-foreground ms-2 font-mono text-[11px]">
                  {y.n.toLocaleString("en-US")}
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
    </div>
  );
}

/** A one-line description of the current scope, for headings and empty states. */
export function useScopeLabel(modelName?: string, brandName?: string): string {
  const filters = useFilters();
  const variant = filters.get("variant");
  const year = filters.get("year");
  if (!filters.get("brand") && !filters.get("model")) return "کل بازار";
  const parts = [brandName, modelName].filter(Boolean);
  if (variant) parts.push("تیپ انتخاب‌شده");
  if (year) parts.push(`سال ${year}`);
  return parts.join(" · ") || "کل بازار";
}
