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
        color: c.close >= c.open ? "rgba(16,185,129,0.35)" : "rgba(239,68,68,0.35)",
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
          textColor: "rgba(255,255,255,0.4)",
          fontFamily: "'Inter', -apple-system, sans-serif",
        },
        grid: {
          vertLines: { color: "rgba(255,255,255,0.04)" },
          horzLines: { color: "rgba(255,255,255,0.04)" },
        },
        rightPriceScale: {
          borderColor: "rgba(255,255,255,0.06)",
          scaleMargins: { top: 0.1, bottom: 0.25 },
        },
        timeScale: {
          borderColor: "rgba(255,255,255,0.06)",
          timeVisible: true,
          secondsVisible: false,
        },
        crosshair: {
          vertLine: { color: "rgba(255,255,255,0.1)", labelBackgroundColor: "#1a1a1a" },
          horzLine: { color: "rgba(255,255,255,0.1)", labelBackgroundColor: "#1a1a1a" },
        },
      });

      chartRef.current = chart;

      /* ── Candlestick series ────────────────────────────── */
      const candleSeries = chart.addCandlestickSeries({
        upColor: "#10b981",
        downColor: "#ef4444",
        borderUpColor: "#10b981",
        borderDownColor: "#ef4444",
        wickUpColor: "#10b981",
        wickDownColor: "#ef4444",
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
          color: "rgba(251,191,36,0.7)",
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
    <div className="relative rounded-xl border border-white/[0.06] bg-[#0b0f19] overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.06]">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-white/80">
            {asset.replace("USDT", "/USDT")}
          </span>
          <span className="text-xs text-neutral-500">1H</span>
        </div>
        {loading && (
          <span className="text-xs text-neutral-500 animate-pulse">불러오는 중...</span>
        )}
      </div>

      {/* Chart container */}
      <div ref={containerRef} className="h-[380px] w-full" />

      {/* Loading skeleton overlay */}
      {loading && !error && (
        <div className="absolute inset-0 top-[45px] flex items-center justify-center bg-[#0b0f19]/80">
          <div className="space-y-3 w-3/4">
            <div className="h-2 rounded bg-neutral-800 animate-pulse" />
            <div className="h-2 rounded bg-neutral-800 animate-pulse w-5/6" />
            <div className="h-2 rounded bg-neutral-800 animate-pulse w-4/6" />
            <div className="h-2 rounded bg-neutral-800 animate-pulse w-5/6" />
            <div className="h-2 rounded bg-neutral-800 animate-pulse w-3/6" />
          </div>
        </div>
      )}

      {/* Error state */}
      {error && (
        <div className="absolute inset-0 top-[45px] flex items-center justify-center bg-[#0b0f19]/80">
          <div className="text-center">
            <p className="text-sm text-neutral-500">{error}</p>
            <p className="text-xs text-neutral-600 mt-1">
              시장 데이터 서비스에 연결할 수 없습니다
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
