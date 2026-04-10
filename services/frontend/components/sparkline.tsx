"use client";

import { motion } from "framer-motion";

interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  color?: string;
  showArea?: boolean;
  className?: string;
  animate?: boolean;
}

export function Sparkline({
  data,
  width = 80,
  height = 28,
  color = "currentColor",
  showArea = true,
  className = "",
  animate = true,
}: SparklineProps) {
  if (!data || data.length < 2) return null;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const pad = 2;

  const points = data.map((v, i) => ({
    x: pad + (i / (data.length - 1)) * (width - pad * 2),
    y: pad + (1 - (v - min) / range) * (height - pad * 2),
  }));

  const linePath = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(" ");

  const areaPath = `${linePath} L ${points[points.length - 1].x.toFixed(1)},${height} L ${points[0].x.toFixed(1)},${height} Z`;

  const gradientId = `spark-grad-${Math.random().toString(36).slice(2, 8)}`;
  const isPositive = data[data.length - 1] >= data[0];
  const strokeColor = color !== "currentColor" ? color : isPositive ? "#10b981" : "#ef4444";

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={`inline-block ${className}`}
      role="img"
      aria-label={`Trend: ${isPositive ? "up" : "down"} ${((data[data.length - 1] - data[0]) / (data[0] || 1) * 100).toFixed(1)}%`}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={strokeColor} stopOpacity={0.25} />
          <stop offset="100%" stopColor={strokeColor} stopOpacity={0} />
        </linearGradient>
      </defs>
      {showArea && (
        <motion.path
          d={areaPath}
          fill={`url(#${gradientId})`}
          initial={animate ? { opacity: 0 } : undefined}
          animate={animate ? { opacity: 1 } : undefined}
          transition={{ duration: 0.6, delay: 0.3 }}
        />
      )}
      <motion.path
        d={linePath}
        fill="none"
        stroke={strokeColor}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={animate ? { pathLength: 0 } : undefined}
        animate={animate ? { pathLength: 1 } : undefined}
        transition={{ duration: 0.8, ease: "easeOut" }}
      />
      {/* End dot */}
      <motion.circle
        cx={points[points.length - 1].x}
        cy={points[points.length - 1].y}
        r={2}
        fill={strokeColor}
        initial={animate ? { opacity: 0, scale: 0 } : undefined}
        animate={animate ? { opacity: 1, scale: 1 } : undefined}
        transition={{ delay: 0.8, duration: 0.3 }}
      />
    </svg>
  );
}
