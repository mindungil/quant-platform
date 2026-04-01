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
      layout: { background: { color: "transparent" }, textColor: "#737373" },
      grid: { vertLines: { color: "#F5F5F5" }, horzLines: { color: "#F5F5F5" } }
    });
    const series = chart.addAreaSeries({ lineColor: "#171717", topColor: "rgba(23,23,23,0.12)", bottomColor: "rgba(23,23,23,0.02)" });
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
