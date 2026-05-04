"use client";

import { useEffect } from "react";
import Link from "next/link";
import { motion } from "framer-motion";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Surface to console for triage; the digest is what server logs index by.
    console.error("[error.tsx]", error);
  }, [error]);

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-ink overflow-hidden">
      <div className="pointer-events-none fixed inset-0">
        <div className="bg-orb-light" style={{ top: "-100px", right: "-80px", width: "min(500px, 80vw)", height: "min(500px, 80vw)", background: "radial-gradient(circle, rgba(255,107,107,0.04), transparent 70%)" }} />
        <div className="absolute inset-0 opacity-[0.05]" style={{
          backgroundImage: "linear-gradient(rgba(255,107,107,0.4) 1px, transparent 1px), linear-gradient(90deg, rgba(255,107,107,0.4) 1px, transparent 1px)",
          backgroundSize: "64px 64px",
        }} />
      </div>

      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="relative z-10 w-full max-w-[480px] px-5 sm:px-6"
      >
        <div className="mb-6 flex items-baseline gap-3">
          <span className="amber-led-static led-coral" aria-hidden />
          <p className="font-mono text-[10px] tracking-[0.22em] uppercase text-coral">FAULT // RUNTIME</p>
        </div>

        <div className="bg-ink-50 border border-coral/40 p-6">
          <p className="label-eyebrow text-paper-mute mb-3">UNHANDLED_EXCEPTION</p>
          <h1 className="font-mono text-lg font-bold tracking-[0.08em] text-paper uppercase">
            오류가 발생했습니다
          </h1>
          <p className="mt-3 font-prose text-sm text-paper-dim leading-relaxed">
            예기치 못한 문제로 페이지를 표시할 수 없습니다.
          </p>

          {error.digest && (
            <p className="mt-4 px-3 py-2 border border-rule bg-ink font-mono text-[10px] tabular text-paper-low">
              digest: {error.digest}
            </p>
          )}

          <div className="mt-6 flex gap-2">
            <button
              onClick={reset}
              className="btn-primary flex-1"
            >
              ↻ RETRY
            </button>
            <Link
              href="/dashboard"
              className="flex-1 inline-flex items-center justify-center border border-rule-loud font-mono text-[11px] uppercase tracking-[0.16em] text-paper-dim hover:text-amber hover:border-amber transition-colors"
            >
              → DASHBOARD
            </Link>
          </div>
        </div>
      </motion.div>
    </div>
  );
}
