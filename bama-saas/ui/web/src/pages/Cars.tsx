/**
 * Every car on Bama, and the good deals among them, as one tab.
 *
 * Listings and Deals were two tabs over the same cars with the same filter
 * panel. One unified mode bar now picks the population; the filters live in
 * the URL, so they carry across when it switches.
 */
import { useMlEnabled } from "../auth";
import { Deals, DEAL_TABS, NotifierPanel } from "./Deals";
import { Explorer } from "./Explorer";
import { useFilters } from "../filters";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";

export function Cars() {
  const filters = useFilters();
  const mlEnabled = useMlEnabled();
  const dealsOnly = filters.get("deals") === "1";
  const dealTabs = mlEnabled ? DEAL_TABS : DEAL_TABS.filter((t) => t.id !== "ml");
  const rawBand = filters.get("band");
  const band = dealTabs.some((t) => t.id === rawBand) ? rawBand! : "top";
  const activeMode = dealsOnly ? band : "ads";

  return (
    <div className="stack">
      <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <Tabs
          className="min-w-0"
          value={activeMode}
          onValueChange={(next) => {
            if (next === "ads") {
              // Band and confidence exist only on the deal board, so leaving it
              // drops them. The sort stays: the board ignores it, and the
              // listings get it back when switching back to all ads.
              filters.set({ deals: null, page: null, band: null, confidence: null });
            } else {
              filters.set({ deals: "1", band: next === "top" ? null : next, page: null });
            }
          }}
        >
          <TabsList aria-label="نمایش خودروها">
            <TabsTrigger value="ads">همه آگهی‌ها</TabsTrigger>
            {dealTabs.map((t) => (
              <TabsTrigger key={t.id} value={t.id}>{t.label}</TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        {dealsOnly && (
          <div className="shrink-0 self-end sm:self-auto">
            <NotifierPanel />
          </div>
        )}
      </div>
      {dealsOnly ? <Deals /> : <Explorer />}
    </div>
  );
}
