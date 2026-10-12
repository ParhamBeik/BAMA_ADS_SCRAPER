/**
 * Every car on Bama, and the good deals among them, as one tab.
 *
 * Listings and Deals were two tabs over the same cars with the same filter
 * panel. One switch now picks the population; the filters live in the URL, so
 * they carry across when it flips.
 */
import { useId } from "react";
import { Deals } from "./Deals";
import { Explorer } from "./Explorer";
import { useFilters } from "../filters";
import { Switch } from "../components/ui/switch";

export function Cars() {
  const filters = useFilters();
  const dealsOnly = filters.get("deals") === "1";
  const id = useId();
  return (
    <div className="stack">
      <div className="row" style={{ gap: 8 }}>
        <Switch
          id={id}
          checked={dealsOnly}
          // Page, ordering and band belong to one list and mean nothing in the other.
          onCheckedChange={(on) =>
            filters.set({ deals: on ? "1" : null, page: null, ordering: null, band: null })}
        />
        <label htmlFor={id}>فقط معامله‌های خوب</label>
      </div>
      {dealsOnly ? <Deals /> : <Explorer />}
    </div>
  );
}
