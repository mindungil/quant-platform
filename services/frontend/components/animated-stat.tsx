"use client";

import { motion, AnimatePresence } from "framer-motion";
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
}: AnimatedStatProps) {
  const [displayed, setDisplayed] = useState(value);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  const prevValue = useRef(value);

  useEffect(() => {
    if (value !== prevValue.current) {
      setFlash(value > prevValue.current ? "up" : "down");
      const timer = setTimeout(() => setFlash(null), 600);
      prevValue.current = value;

      // Animate count
      const start = displayed;
      const end = value;
      const duration = 400;
      const startTime = Date.now();
      const animate = () => {
        const elapsed = Date.now() - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
        setDisplayed(start + (end - start) * eased);
        if (progress < 1) requestAnimationFrame(animate);
      };
      requestAnimationFrame(animate);
      return () => clearTimeout(timer);
    }
  }, [value]);

  const isPos = positive ?? value >= 0;
  const colorClass = isPos ? "text-emerald-400" : "text-red-400";
  const flashBg = flash === "up"
    ? "bg-emerald-500/10"
    : flash === "down"
    ? "bg-red-500/10"
    : "bg-transparent";

  return (
    <div className={`space-y-1.5 ${className}`}>
      <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">
        {label}
      </p>
      <div className="flex items-end gap-3">
        <motion.div
          className={`rounded-md px-1.5 py-0.5 transition-colors duration-300 ${flashBg}`}
        >
          <span className={`font-mono text-xl font-semibold tabular-nums ${colorClass}`}>
            {isPos && value > 0 ? "+" : ""}
            {formatValue(displayed, decimals, format, prefix, suffix)}
          </span>
        </motion.div>
        {trend && trend.length > 1 && (
          <Sparkline
            data={trend}
            width={64}
            height={24}
            color={isPos ? "#10b981" : "#ef4444"}
          />
        )}
      </div>
    </div>
  );
}
