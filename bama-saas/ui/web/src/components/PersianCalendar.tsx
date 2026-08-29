/**
 * The Persian-calendar month grid, in its own chunk.
 *
 * Separate from WindowPicker purely so the calendar and its locale tables load
 * when the popover opens rather than on first paint — see the lazy import there.
 *
 * `Calendar`, not `DatePicker`: the popover is already the thing that opens and
 * closes, so the input-plus-dropdown variant would nest one popover in another.
 *
 * It is styled through the token overrides in styles.css rather than one of the
 * library's bundled themes, all of which hardcode their own colours and would
 * make this the one surface that does not follow the light/dark switch.
 */
import { useEffect, useRef } from "react";
import { Calendar, type DateObject } from "react-multi-date-picker";
import persian from "react-date-object/calendars/persian";
import persian_fa from "react-date-object/locales/persian_fa";

export default function PersianCalendar({
  onPick,
}: {
  onPick: (date: DateObject) => void;
}) {
  const host = useRef<HTMLDivElement>(null);

  // The library's month arrows are bare `<span>`s carrying only an English
  // `aria-roledescription="button to navigate left"` — no role, no name — so a
  // screen reader announced two unlabelled elements on an otherwise Persian
  // calendar. It renders them itself and exposes no prop for this, so they are
  // labelled after mount. Direction is by position, not by "left"/"right":
  // this calendar is RTL and previous is the *right*-hand one.
  useEffect(() => {
    const arrows = host.current?.querySelectorAll<HTMLElement>(".rmdp-arrow-container");
    arrows?.forEach((arrow, i) => {
      arrow.setAttribute("role", "button");
      arrow.setAttribute("tabindex", "0");
      arrow.setAttribute("aria-label", i === 0 ? "ماه بعد" : "ماه قبل");
      arrow.removeAttribute("aria-roledescription");
    });
  });

  return (
    <div ref={host}>
      <Calendar
        calendar={persian}
        locale={persian_fa}
        // Nothing can be measured from the future, and the series cannot go back
        // further than the crawl does anyway.
        maxDate={new Date()}
        onChange={(date) => {
          if (date && !Array.isArray(date)) onPick(date);
        }}
        shadow={false}
      />
    </div>
  );
}
