/**
 * Everything personal in one tab: saved cars, alerts, and the account.
 *
 * These were a nav tab, a header icon, a bell and two menu items; the bell's
 * unread count now sits on this tab instead.
 */
import { Account } from "./Account";
import { Alerts } from "./Alerts";
import { Saved } from "./Saved";
import { useFilters } from "../filters";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";

const SECTIONS = [
  { id: "saved", label: "ذخیره‌شده", Page: Saved },
  { id: "alerts", label: "هشدارها", Page: Alerts },
  { id: "account", label: "حساب", Page: Account },
] as const;

export function Mine() {
  const filters = useFilters();
  const section = SECTIONS.find((s) => s.id === filters.get("tab")) ?? SECTIONS[0];
  return (
    <div className="stack">
      <Tabs value={section.id}
            onValueChange={(next) => filters.set({ tab: next === "saved" ? null : next })}>
        <TabsList aria-label="بخش‌های من">
          {SECTIONS.map((s) => <TabsTrigger key={s.id} value={s.id}>{s.label}</TabsTrigger>)}
        </TabsList>
      </Tabs>
      <section.Page />
    </div>
  );
}
