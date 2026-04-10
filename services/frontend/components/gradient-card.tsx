"use client";

import { motion } from "framer-motion";
import type { ReactNode } from "react";

interface GradientCardProps {
  children: ReactNode;
  accent?: "green" | "red" | "neutral" | "blue";
  glow?: boolean;
  className?: string;
  delay?: number;
}

const accentColors = {
  green:   { border: "border-emerald-500/10", bar: "from-emerald-500/20 via-emerald-500/5 to-transparent", glow: "rgba(16, 185, 129, 0.06)" },
  red:     { border: "border-red-500/10", bar: "from-red-500/20 via-red-500/5 to-transparent", glow: "rgba(239, 68, 68, 0.06)" },
  neutral: { border: "border-white/[0.08]", bar: "from-white/10 via-white/3 to-transparent", glow: "rgba(255, 255, 255, 0.04)" },
  blue:    { border: "border-blue-500/10", bar: "from-blue-500/20 via-blue-500/5 to-transparent", glow: "rgba(59, 130, 246, 0.06)" },
};

export function GradientCard({
  children,
  accent = "neutral",
  glow = false,
  className = "",
  delay = 0,
}: GradientCardProps) {
  const colors = accentColors[accent];

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay, ease: "easeOut" }}
      className={`
        relative overflow-hidden rounded-xl border ${colors.border}
        bg-white/[0.02] p-5
        transition-all duration-200
        hover:border-white/[0.12] hover:bg-white/[0.04]
        ${glow ? "hover:shadow-lg" : ""}
        ${className}
      `}
      style={glow ? { boxShadow: `0 0 30px ${colors.glow}` } : undefined}
    >
      {/* Top accent bar */}
      <div
        className={`absolute inset-x-0 top-0 h-[2px] bg-gradient-to-r ${colors.bar}`}
      />
      {children}
    </motion.div>
  );
}
