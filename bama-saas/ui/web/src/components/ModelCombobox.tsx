/**
 * "Which car" — one searchable box over every model, with how many listings
 * each has.
 *
 * The count is the point: it turns "is this the right one of four similar
 * names" into a fact rather than a guess. It is counted over the same population
 * the Explorer lists, so the number beside a model cannot promise listings the
 * next screen does not show.
 *
 * Shared by the filter toolbar and the analysis scope picker. They want the same
 * control and differ only in what they write to the URL, so that is the prop.
 *
 * Debounced, because it fires per keystroke against a counting query.
 */
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronsUpDown } from "lucide-react";
import { api } from "@/api";
import { qs } from "@/filters";
import { Fa } from "@/ui";
import { Button } from "@/components/ui/button";
import {
  Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export interface ModelRow {
  id: number;
  name_fa: string;
  brand_slug: string;
  brand_name: string;
  ad_count: number;
}

/**
 * The label for the currently-selected model.
 *
 * Resolved by id rather than looked up in the search results: the ranked list
 * stops at 60 rows, so a link shared for an unpopular model would otherwise open
 * a picker that could not name the car the page is about.
 */
export function useModelLabel(modelId?: string) {
  const query = useQuery({
    queryKey: ["model", modelId],
    enabled: Boolean(modelId),
    staleTime: Infinity, // a model's name does not change
    queryFn: ({ signal }) => api.get<ModelRow[]>(`/api/models/${qs({ id: modelId })}`, signal),
  });
  return query.data?.[0];
}

export function ModelCombobox({
  value,
  brand,
  onSelect,
  placeholder = "نام مدل را بنویسید…",
  className,
}: {
  /** Selected model id, as it sits in the URL. */
  value?: string;
  /** Optional coarse filter, so a chosen brand narrows the list. */
  brand?: string;
  /** `null` means "all models". */
  onSelect: (model: ModelRow | null) => void;
  placeholder?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [term, setTerm] = useState("");
  const selected = useModelLabel(value);

  useEffect(() => {
    const id = setTimeout(() => setTerm(draft), 220);
    return () => clearTimeout(id);
  }, [draft]);

  const models = useQuery({
    queryKey: ["model-search", term, brand],
    queryFn: ({ signal }) =>
      api.get<ModelRow[]>(`/api/models/${qs({ q: term, brand })}`, signal),
  });

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className={cn("w-full justify-between font-normal", className)}
        >
          <span className="truncate">
            {selected
              ? <Fa>{selected.brand_name} {selected.name_fa}</Fa>
              : <span className="text-muted-foreground">{placeholder}</span>}
          </span>
          <ChevronsUpDown className="size-4 flex-none opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(22rem,90vw)] p-0">
        {/* The server already ranks and filters; cmdk must not filter a second
            time or a search for a Persian name it scores differently comes back
            empty while the API found matches. */}
        <Command shouldFilter={false}>
          <CommandInput
            placeholder="جست‌وجوی مدل…"
            value={draft}
            onValueChange={setDraft}
          />
          <CommandList>
            {models.isLoading ? (
              <div className="text-muted-foreground p-3 text-sm">در حال جست‌وجو…</div>
            ) : (
              <CommandEmpty>مدلی پیدا نشد</CommandEmpty>
            )}
            <CommandGroup>
              {value && (
                <CommandItem
                  value="__all__"
                  onSelect={() => { onSelect(null); setDraft(""); setOpen(false); }}
                >
                  همه مدل‌ها
                </CommandItem>
              )}
              {models.data?.map((model) => (
                <CommandItem
                  key={model.id}
                  value={String(model.id)}
                  onSelect={() => { onSelect(model); setDraft(""); setOpen(false); }}
                >
                  <Check
                    className={cn(
                      "size-4",
                      String(model.id) === value ? "opacity-100" : "opacity-0",
                    )}
                  />
                  <span className="truncate">
                    <Fa>{model.brand_name} {model.name_fa}</Fa>
                  </span>
                  <span className="text-muted-foreground ms-auto font-mono text-[11.5px]">
                    {model.ad_count.toLocaleString("en-US")}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
