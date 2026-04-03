"use client";

import { useEffect, useRef } from "react";

export function ChartPlaceholder() {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    let chart: ReturnType<typeof import("lightweight-charts").createChart> | null = null;
    import("lightweight-charts").then(({ createChart }) => {
      if (!ref.current) return;
      chart = createChart(ref.current, {
        width: ref.current.clientWidth,
        height: 280,
        layout: {
          background: { color: "transparent" },
          textColor: "rgba(255,255,255,0.3)",
        },
        grid: {
          vertLines: { color: "rgba(255,255,255,0.04)" },
          horzLines: { color: "rgba(255,255,255,0.04)" },
        },
        rightPriceScale: { borderColor: "rgba(255,255,255,0.06)" },
        timeScale: { borderColor: "rgba(255,255,255,0.06)" },
      });
      const series = chart.addAreaSeries({
        lineColor: "#fff",
        topColor: "rgba(255,255,255,0.08)",
        bottomColor: "rgba(255,255,255,0.01)",
        lineWidth: 2,
      });
      // Empty — will be populated with real data when available
      series.setData([]);
    });
    return () => { chart?.remove(); };
  }, []);

  return (
    <div ref={ref} className="h-[280px] w-full rounded-xl border border-white/[0.06]" />
  );
}
