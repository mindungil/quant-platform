"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";

export function SplashScreen({ children }: { children: React.ReactNode }) {
  const [showSplash, setShowSplash] = useState(true);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // Skip if already seen this session
    if (sessionStorage.getItem("splash_seen")) {
      setShowSplash(false);
      return;
    }

    // Show splash for 2.5 seconds then fade out
    const timer = setTimeout(() => {
      setShowSplash(false);
      sessionStorage.setItem("splash_seen", "1");
    }, 2500);

    setReady(true);
    return () => clearTimeout(timer);
  }, []);

  if (!showSplash) return <>{children}</>;

  return (
    <AnimatePresence mode="wait">
      {showSplash && ready && (
        <motion.div
          key="splash"
          initial={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.5 }}
          className="fixed inset-0 z-[100] flex flex-col items-center justify-center bg-zinc-950"
        >
          {/* Background subtle radial glow */}
          <div className="absolute inset-0 overflow-hidden">
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 0.15 }}
              transition={{ duration: 1.5 }}
              className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] rounded-full"
              style={{
                background:
                  "radial-gradient(circle, rgba(255,255,255,0.08), transparent 70%)",
                filter: "blur(80px)",
              }}
            />
          </div>

          {/* Logo "Q" — springs in from scaled-down */}
          <motion.div
            initial={{ scale: 0.5, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{
              type: "spring",
              stiffness: 200,
              damping: 20,
              delay: 0.2,
            }}
            className="relative z-10 flex h-20 w-20 items-center justify-center rounded-2xl bg-white"
          >
            <span className="text-4xl font-black text-black tracking-tighter">
              Q
            </span>
          </motion.div>

          {/* Platform name */}
          <motion.h1
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.6, duration: 0.5 }}
            className="relative z-10 mt-6 text-2xl font-bold tracking-[0.3em] text-white"
          >
            QUANT
          </motion.h1>

          {/* Tagline */}
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 0.5 }}
            transition={{ delay: 1.0, duration: 0.5 }}
            className="relative z-10 mt-2 text-xs tracking-[0.2em] text-[#a1a1a1] uppercase"
          >
            AI Autonomous Trading
          </motion.p>

          {/* Loading bar */}
          <motion.div
            initial={{ width: 0 }}
            animate={{ width: "120px" }}
            transition={{ delay: 0.8, duration: 1.5, ease: "easeInOut" }}
            className="relative z-10 mt-8 h-[1px] bg-gradient-to-r from-transparent via-white/40 to-transparent"
          />
        </motion.div>
      )}
    </AnimatePresence>
  );
}
