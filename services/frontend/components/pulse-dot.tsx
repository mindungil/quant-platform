"use client";

import { motion } from "framer-motion";

interface PulseDotProps {
  status: "active" | "warning" | "critical" | "inactive";
  size?: "sm" | "md";
  className?: string;
}

const statusColors = {
  active:   { dot: "bg-emerald-400", ring: "bg-emerald-400" },
  warning:  { dot: "bg-amber-400", ring: "bg-amber-400" },
  critical: { dot: "bg-red-400", ring: "bg-red-400" },
  inactive: { dot: "bg-zinc-500", ring: "bg-zinc-500" },
};

const sizes = {
  sm: { dot: "h-1.5 w-1.5", ring: "h-3 w-3" },
  md: { dot: "h-2 w-2", ring: "h-4 w-4" },
};

export function PulseDot({ status, size = "sm", className = "" }: PulseDotProps) {
  const colors = statusColors[status];
  const dims = sizes[size];
  const shouldPulse = status === "active" || status === "critical";

  return (
    <span className={`relative inline-flex items-center justify-center ${dims.ring} ${className}`}>
      {shouldPulse && (
        <motion.span
          className={`absolute rounded-full ${colors.ring} ${dims.ring}`}
          animate={{
            scale: [1, 1.8, 1],
            opacity: [0.4, 0, 0.4],
          }}
          transition={{
            duration: status === "critical" ? 1.2 : 2.0,
            repeat: Infinity,
            ease: "easeInOut",
          }}
        />
      )}
      <span className={`relative rounded-full ${colors.dot} ${dims.dot}`} />
    </span>
  );
}
