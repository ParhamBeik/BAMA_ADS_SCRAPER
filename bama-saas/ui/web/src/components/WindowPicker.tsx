/**
 * How far back an analysis looks.
 *
 * Every analytics endpoint takes `?days=`, so that is what this writes into the
 * URL — presets for the common answers, and a Persian-calendar date for "since
 * a specific day", converted to a day count on the way out.
 *
 * A *start* date rather than a range on purpose: the series all end at the most
 * recent data there is, so a second "to" field would be a control that either
 * does nothing or quietly discards the newest days. One honest field beats two
 * where one is a lie.
 *
 * The calendar is Persian, not Gregorian. This is a Persian-first product and
 * the default calendar that ships with most component kits would have people
 * converting dates in their head to use it.
 */
import { lazy, Suspense, useState } from "react";
import type { DateObject } from "react-multi-date-picker";
import { CalendarDays } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useFilters } from "@/filters";

/**
 * Split out and loaded on open.
 *
 * The calendar and its two locale tables are ~200kB, they appear on the home
 * page, and most sessions never open the popover at all — eager-importing them
 * put a fifth of the initial bundle behind a control nobody had clicked yet.
 */
const PersianCalendar = lazy(() => import("./PersianCalendar"));

const PRESETS: [string, number][] = [
  ["۷ روز", 7],
  ["۳۰ روز", 30],
  ["۹۰ روز", 90],
  ["یک سال", 365],
];

/** Whole days between a chosen day and today, floored at the shortest window
 *  any of these endpoints will accept. */
function daysSince(date: DateObject): number {
  const chosen = new Date(date.toDate());
  chosen.setHours(0, 0, 0, 0);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.max(2, Math.round((today.getTime() - chosen.getTime()) / 86_400_000));
}

export function WindowPicker({ defaultDays = 30 }: { defaultDays?: number }) {
  const filters = useFilters();
  const [open, setOpen] = useState(false);
  const days = filters.getInt("days") ?? defaultDays;
  const isPreset = PRESETS.some(([, d]) => d === days);

  return (
    <div className="flex flex-wrap items-center gap-1">
      {PRESETS.map(([label, value]) => (
        <Button
          key={value}
          size="sm"
          variant={days === value ? "secondary" : "ghost"}
          aria-pressed={days === value}
          onClick={() => filters.set({ days: value === defaultDays ? null : value })}
        >
          {label}
        </Button>
      ))}
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button size="sm" variant={isPreset ? "ghost" : "secondary"}>
            <CalendarDays className="size-4" />
            {isPreset ? "از تاریخ…" : `${days} روز گذشته`}
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-auto p-2">
          <p className="text-muted-foreground mb-2 px-1 text-xs">
            از چه تاریخی به بعد بررسی شود؟
          </p>
          <Suspense
            fallback={<div className="text-muted-foreground p-6 text-center text-sm">…</div>}
          >
            <PersianCalendar
              onPick={(date) => {
                filters.set({ days: daysSince(date) });
                setOpen(false);
              }}
            />
          </Suspense>
        </PopoverContent>
      </Popover>
    </div>
  );
}
