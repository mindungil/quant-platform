"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import type { Time } from "lightweight-charts";
import { gatewayFetch } from "../lib/api";

/* ── Types ─────────────────────────────────────────────────────── */

interface CandleRaw {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  ema_21?: number;
}

interface CandleFormatted {
  time: Time;
  open: number;
  high: number;
  low: number;
  close: number;
}

interface VolumeFormatted {
  time: Time;
  value: number;
  color: string;
}

/* ── Component ─────────────────────────────────────────────────── */

export function MarketChart({ asset = "BTCUSDT" }: { asset?: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<ReturnType<typeof import("lightweight-charts").createChart> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const destroyChart = useCallback(() => {
    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!containerRef.current) return;
    let cancelled = false;

    setLoading(true);
    setError(null);
    destroyChart();

    (async () => {
      /* ── Fetch data ────────────────────────────────────── */
      let rawCandles: CandleRaw[];
      try {
        rawCandles = await gatewayFetch(`/market-data/${asset}/history?limit=200`);
      } catch {
        if (!cancelled) {
          setError("데이터 없음");
          setLoading(false);
        }
        return;
      }

      if (cancelled || !containerRef.current) return;
      if (!Array.isArray(rawCandles) || rawCandles.length === 0) {
        setError("데이터 없음");
        setLoading(false);
        return;
      }

      /* ── Convert to lightweight-charts format ──────────── */
      const candles: CandleFormatted[] = rawCandles.map((c) => ({
        time: Math.floor(new Date(c.timestamp).getTime() / 1000) as Time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }));

      const volumes: VolumeFormatted[] = rawCandles.map((c) => ({
        time: Math.floor(new Date(c.timestamp).getTime() / 1000) as Time,
        value: c.volume,
        color: c.close >= c.open ? "rgba(72,213,151,0.35)" : "rgba(255,92,92,0.35)",
      }));

      // EMA data (if available from features)
      const emaData = rawCandles
        .filter((c) => c.ema_21 != null)
        .map((c) => ({
          time: Math.floor(new Date(c.timestamp).getTime() / 1000) as Time,
          value: c.ema_21!,
        }));

      /* ── Create chart ──────────────────────────────────── */
      const { createChart } = await import("lightweight-charts");

      if (cancelled || !containerRef.current) return;

      const chart = createChart(containerRef.current, {
        width: containerRef.current.clientWidth,
        height: 380,
        layout: {
          background: { color: "transparent" },
          textColor: "#8a847a",
          fontFamily: "'JetBrains Mono', monospace",
        },
        grid: {
          vertLines: { color: "rgba(255, 176, 0, 0.04)" },
          horzLines: { color: "rgba(245, 239, 230, 0.04)" },
        },
        rightPriceScale: {
          borderColor: "rgba(245, 239, 230, 0.08)",
          scaleMargins: { top: 0.1, bottom: 0.25 },
        },
        timeScale: {
          borderColor: "rgba(245, 239, 230, 0.08)",
          timeVisible: true,
          secondsVisible: false,
        },
        crosshair: {
          vertLine: { color: "rgba(255, 176, 0, 0.35)", labelBackgroundColor: "#1a1614" },
          horzLine: { color: "rgba(255, 176, 0, 0.35)", labelBackgroundColor: "#1a1614" },
        },
      });

      chartRef.current = chart;

      /* ── Candlestick series ────────────────────────────── */
      const candleSeries = chart.addCandlestickSeries({
        upColor: "#48d597",
        downColor: "#ff5c5c",
        borderUpColor: "#48d597",
        borderDownColor: "#ff5c5c",
        wickUpColor: "#48d597",
        wickDownColor: "#ff5c5c",
      });
      candleSeries.setData(candles);

      /* ── Volume histogram ──────────────────────────────── */
      const volumeSeries = chart.addHistogramSeries({
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
      });
      volumeSeries.priceScale().applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });
      volumeSeries.setData(volumes);

      /* ── EMA overlay ───────────────────────────────────── */
      if (emaData.length > 0) {
        const emaSeries = chart.addLineSeries({
          color: "rgba(255, 176, 0, 0.85)",
          lineWidth: 1,
          crosshairMarkerVisible: false,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        emaSeries.setData(emaData);
      }

      chart.timeScale().fitContent();

      /* ── Resize handler ────────────────────────────────── */
      const onResize = () => {
        if (containerRef.current && chartRef.current) {
          chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
        }
      };
      window.addEventListener("resize", onResize);

      if (!cancelled) setLoading(false);

      // Cleanup resize listener on unmount
      return () => {
        window.removeEventListener("resize", onResize);
      };
    })();

    return () => {
      cancelled = true;
      destroyChart();
    };
  }, [asset, destroyChart]);

  /* ── Render ──────────────────────────────────────────────────── */

  return (
    <div className="relative border border-rule bg-ink-50 overflow-hidden">
      {/* Header — editorial caption */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-rule">
        <div className="flex items-baseline gap-3">
          <span className="font-display-italic text-xl text-paper">
            {asset.replace("USDT", "")}
          </span>
          <span className="font-mono text-[10px] tracking-[0.18em] uppercase text-paper-mute">
            / USDT · 1H
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="amber-led" aria-hidden />
          <span className="font-mono text-[10px] tracking-[0.18em] uppercase text-paper-mute">
            {loading ? "Loading" : "Live"}
          </span>
        </div>
      </div>

      <div ref={containerRef} className="h-[380px] w-full" />

      {loading && !error && (
        <div className="absolute inset-0 top-[49px] flex items-center justify-center bg-ink-50/85">
          <div className="space-y-3 w-2/3">
            <div className="skeleton h-2" />
            <div className="skeleton h-2 w-5/6" />
            <div className="skeleton h-2 w-4/6" />
            <div className="skeleton h-2 w-5/6" />
            <div className="skeleton h-2 w-3/6" />
          </div>
        </div>
      )}

      {error && (
        <div className="absolute inset-0 top-[49px] flex items-center justify-center bg-ink-50/90">
          <div className="text-center">
            <p className="font-display-italic text-xl text-paper">{error}</p>
            <p className="mt-2 label-eyebrow">
              market data offline
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
