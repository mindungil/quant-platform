"use client";

import { useEffect, useRef } from "react";
import { createChart } from "lightweight-charts";

export function ChartPlaceholder() {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      width: ref.current.clientWidth,
      height: 280,
      layout: { background: { color: "transparent" }, textColor: "#eef7fb" },
      grid: { vertLines: { color: "rgba(255,255,255,0.08)" }, horzLines: { color: "rgba(255,255,255,0.08)" } }
    });
    const series = chart.addAreaSeries({ lineColor: "#f6bd60", topColor: "rgba(246,189,96,0.45)", bottomColor: "rgba(246,189,96,0.05)" });
    series.setData([
      { time: "2026-03-26", value: 12 },
      { time: "2026-03-27", value: 15 },
      { time: "2026-03-28", value: 11 },
      { time: "2026-03-29", value: 18 },
      { time: "2026-03-30", value: 17 }
    ]);
    return () => chart.remove();
  }, []);

  return <div ref={ref} className="h-[280px] w-full" />;
}
