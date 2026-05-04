"use client";

import { motion } from "framer-motion";

interface PulseDotProps {
  status: "active" | "warning" | "critical" | "inactive";
  size?: "sm" | "md";
  className?: string;
}

const statusColors = {
  active:   { dot: "bg-mint",   ring: "bg-mint",   shadow: "0 0 10px rgba(72,213,151,0.55)" },
  warning:  { dot: "bg-amber",  ring: "bg-amber",  shadow: "0 0 10px rgba(255,176,0,0.55)" },
  critical: { dot: "bg-coral",  ring: "bg-coral",  shadow: "0 0 10px rgba(255,92,92,0.55)" },
  inactive: { dot: "bg-paper-low", ring: "bg-paper-low", shadow: "none" },
};

const sizes = {
  sm: { dot: "h-1.5 w-1.5", ring: "h-3 w-3" },
  md: { dot: "h-2 w-2", ring: "h-4 w-4" },
};

export function PulseDot({ status, size = "sm", className = "" }: PulseDotProps) {
  const colors = statusColors[status];
  const dims = sizes[size];
  const shouldPulse = status === "active" || status === "critical" || status === "warning";

  return (
    <span className={`relative inline-flex items-center justify-center ${dims.ring} ${className}`}>
      {shouldPulse && (
        <motion.span
          className={`absolute rounded-full ${colors.ring} ${dims.ring} opacity-50`}
          animate={{ scale: [1, 1.9, 1], opacity: [0.45, 0, 0.45] }}
          transition={{
            duration: status === "critical" ? 1.1 : status === "warning" ? 1.4 : 1.8,
            repeat: Infinity,
            ease: "easeInOut",
          }}
        />
      )}
      <span
        className={`relative rounded-full ${colors.dot} ${dims.dot}`}
        style={{ boxShadow: colors.shadow }}
      />
    </span>
  );
}
