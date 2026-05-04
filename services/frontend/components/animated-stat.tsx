"use client";

import { motion } from "framer-motion";
import { useState, useEffect, useRef } from "react";
import { Sparkline } from "./sparkline";

interface AnimatedStatProps {
  label: string;
  value: number;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  trend?: number[];
  positive?: boolean;
  format?: "number" | "percent" | "currency";
  className?: string;
  /** Render hero-sized italic serif (for top-of-page KPIs) */
  hero?: boolean;
}

function formatValue(v: number, decimals: number, format: string, prefix: string, suffix: string): string {
  let str: string;
  if (format === "percent") {
    str = `${(v * 100).toFixed(decimals)}%`;
  } else if (format === "currency") {
    str = v.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  } else {
    str = v.toFixed(decimals);
  }
  return `${prefix}${str}${suffix}`;
}

export function AnimatedStat({
  label,
  value,
  decimals = 2,
  prefix = "",
  suffix = "",
  trend,
  positive,
  format = "number",
  className = "",
  hero = false,
}: AnimatedStatProps) {
  const [displayed, setDisplayed] = useState(value);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  const prevValue = useRef(value);

  useEffect(() => {
    if (value !== prevValue.current) {
      setFlash(value > prevValue.current ? "up" : "down");
      const timer = setTimeout(() => setFlash(null), 600);
      prevValue.current = value;

      const start = displayed;
      const end = value;
      const duration = 500;
      const startTime = Date.now();
      const animate = () => {
        const elapsed = Date.now() - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3);
        setDisplayed(start + (end - start) * eased);
        if (progress < 1) requestAnimationFrame(animate);
      };
      requestAnimationFrame(animate);
      return () => clearTimeout(timer);
    }
  }, [value]);

  const isPos = positive ?? value >= 0;
  const colorClass = value === 0 ? "text-paper" : isPos ? "text-mint" : "text-coral";

  const flashClass = flash === "up" ? "flash-up" : flash === "down" ? "flash-down" : "";

  if (hero) {
    return (
      <div className={`space-y-2 ${className}`}>
        <p className="label-eyebrow-amber">{label}</p>
        <motion.div className={`inline-block px-1 transition-colors ${flashClass}`}>
          <span className={`font-mono text-5xl sm:text-6xl font-medium tracking-[-0.04em] tabular ${colorClass}`}>
            {isPos && value > 0 ? "+" : ""}{formatValue(displayed, decimals, format, prefix, suffix)}
          </span>
        </motion.div>
        {trend && trend.length > 1 && (
          <Sparkline data={trend} width={120} height={28} color={isPos ? "#2dd4bf" : "#f87171"} />
        )}
      </div>
    );
  }

  return (
    <div className={`space-y-2 ${className}`}>
      <p className="label-eyebrow">{label}</p>
      <div className="flex items-end gap-3">
        <motion.div className={`inline-block px-1 transition-colors ${flashClass}`}>
          <span className={`font-mono text-3xl font-medium tracking-[-0.04em] tabular ${colorClass}`}>
            {isPos && value > 0 ? "+" : ""}{formatValue(displayed, decimals, format, prefix, suffix)}
          </span>
        </motion.div>
        {trend && trend.length > 1 && (
          <Sparkline data={trend} width={64} height={24} color={isPos ? "#2dd4bf" : "#f87171"} />
        )}
      </div>
    </div>
  );
}
