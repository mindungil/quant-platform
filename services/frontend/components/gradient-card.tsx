"use client";

import { motion } from "framer-motion";
import type { ReactNode } from "react";

interface GradientCardProps {
  children: ReactNode;
  accent?: "green" | "red" | "neutral" | "blue" | "amber";
  glow?: boolean;
  className?: string;
  delay?: number;
}

const tabColor: Record<string, string> = {
  green:   "bg-mint",
  red:     "bg-coral",
  blue:    "bg-amber",       // re-mapped: blue accents become amber in TERMINAL
  amber:   "bg-amber",
  neutral: "bg-rule-loud",
};
const tabShadow: Record<string, string> = {
  green:   "0 0 12px rgba(45, 212, 191, 0.4)",
  red:     "0 0 12px rgba(248, 113, 113, 0.4)",
  blue:    "0 0 12px rgba(251, 189, 46, 0.5)",
  amber:   "0 0 12px rgba(251, 189, 46, 0.5)",
  neutral: "none",
};

/**
 * Editorial panel — replaces the old "gradient card".
 * Hairline border, amber tab indicator, no rounded chrome.
 */
export function GradientCard({
  children,
  accent = "neutral",
  glow = false,
  className = "",
  delay = 0,
}: GradientCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay, ease: [0.22, 1, 0.36, 1] }}
      className={`relative border border-rule bg-ink-50 px-5 py-5 transition-colors hover:border-rule-loud ${className}`}
    >
      {/* Tab — top-left signal */}
      <span
        aria-hidden
        className={`absolute -top-px left-4 h-[2px] w-12 ${tabColor[accent]}`}
        style={{ boxShadow: glow ? tabShadow[accent] : undefined }}
      />
      {children}
    </motion.div>
  );
}
