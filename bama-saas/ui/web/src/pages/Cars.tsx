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
          // Band and confidence exist only on the deal board, so leaving it
          // drops them. The sort stays: the board ignores it, and the
          // listings get it back when the switch flips off.
          onCheckedChange={(on) =>
            filters.set(on ? { deals: "1", page: null }
                           : { deals: null, page: null, band: null, confidence: null })}
        />
        <label htmlFor={id}>فقط معامله‌های خوب</label>
      </div>
      {dealsOnly ? <Deals /> : <Explorer />}
    </div>
  );
}
