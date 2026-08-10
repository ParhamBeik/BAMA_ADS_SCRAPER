/**
 * One chart component, so every chart in the product shares an axis style, a
 * palette and a tooltip.
 *
 * Colours come from the CSS variables rather than being hardcoded, which is what
 * makes light and dark work without a second chart theme. The old app duplicated
 * hex values in JS next to the same values in CSS, and they had already drifted.
 *
 * Series colours are deliberately semantic: blue is the market series, green and
 * red mean direction, amber means a confidence warning. Reusing them decoratively
 * would make a legend meaningless.
 */
import ReactECharts from "echarts-for-react";
import { useMemo } from "react";
import { useTheme } from "./theme";

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export interface Series {
  name: string;
  data: (number | null)[];
  type?: "line" | "bar";
  color?: string;
  area?: boolean;
}

export function Chart({
  x,
  series,
  height = 260,
  yFormatter,
  stack,
}: {
  x: (string | number)[];
  series: Series[];
  height?: number;
  yFormatter?: (v: number) => string;
  stack?: boolean;
}) {
  const { resolved } = useTheme();

  const option = useMemo(() => {
    const text = cssVar("--text");
    const muted = cssVar("--muted");
    const border = cssVar("--border");
    const panel = cssVar("--panel");
    const palette = [cssVar("--accent"), cssVar("--up"), cssVar("--warn"), cssVar("--down")];

    return {
      grid: { left: 8, right: 12, top: 24, bottom: 8, containLabel: true },
      tooltip: {
        trigger: "axis",
        backgroundColor: panel,
        borderColor: border,
        textStyle: { color: text },
        valueFormatter: yFormatter,
      },
      legend: {
        show: series.length > 1,
        textStyle: { color: muted },
        top: 0,
        icon: "roundRect",
        itemHeight: 8,
      },
      xAxis: {
        type: "category",
        data: x,
        axisLine: { lineStyle: { color: border } },
        axisLabel: { color: muted },
        axisTick: { show: false },
      },
      yAxis: {
        type: "value",
        splitLine: { lineStyle: { color: border, type: "dashed" } },
        axisLabel: { color: muted, formatter: yFormatter },
      },
      series: series.map((s, i) => ({
        name: s.name,
        type: s.type ?? "line",
        data: s.data,
        stack: stack ? "total" : undefined,
        smooth: false,
        showSymbol: false,
        // A gap in the data is drawn as a gap. Connecting across it would invent
        // a trend through days the crawler never covered.
        connectNulls: false,
        lineStyle: { width: 2 },
        itemStyle: { color: s.color ?? palette[i % palette.length] },
        areaStyle: s.area ? { opacity: resolved === "dark" ? 0.16 : 0.08 } : undefined,
      })),
    };
  }, [x, series, yFormatter, stack, resolved]);

  return (
    <ReactECharts
      option={option}
      // Fixed height: charts that size themselves from their container reflow
      // when data arrives and shove the page around.
      style={{ height, width: "100%" }}
      notMerge
      opts={{ renderer: "svg" }}
    />
  );
}
