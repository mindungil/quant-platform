"use client";

import Link from "next/link";
import { motion } from "framer-motion";

export default function NotFound() {
  return (
    <div className="relative flex min-h-screen items-center justify-center bg-ink overflow-hidden">
      <div className="pointer-events-none fixed inset-0">
        <div className="bg-orb-light" style={{ top: "-100px", left: "-80px", width: "min(500px, 80vw)", height: "min(500px, 80vw)" }} />
        <div className="absolute inset-0 opacity-[0.06]" style={{
          backgroundImage: "linear-gradient(rgba(251,189,46,0.4) 1px, transparent 1px), linear-gradient(90deg, rgba(251,189,46,0.4) 1px, transparent 1px)",
          backgroundSize: "64px 64px",
        }} />
      </div>

      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.55, ease: [0.22, 1, 0.36, 1] }}
        className="relative z-10 w-full max-w-[440px] px-5 sm:px-6"
      >
        <div className="mb-6 flex items-baseline gap-3">
          <span className="amber-led" aria-hidden />
          <p className="label-eyebrow-amber">FAULT // 404</p>
        </div>

        <p className="font-mono text-[10rem] leading-none font-bold tracking-tighter text-amber/20 select-none">
          404
        </p>

        <div className="mt-4 bg-ink-50 border border-rule-loud panel-amber-tab p-6">
          <p className="label-eyebrow text-paper-mute mb-3">RESOURCE_NOT_FOUND</p>
          <h1 className="font-mono text-lg font-bold tracking-[0.08em] text-paper uppercase">
            요청한 페이지가 없습니다
          </h1>
          <p className="mt-3 font-prose text-sm text-paper-dim leading-relaxed">
            주소가 변경되었거나 삭제된 페이지일 수 있습니다.
          </p>
          <Link
            href="/dashboard"
            className="btn-primary mt-6 inline-flex items-center justify-center w-full"
          >
            → DASHBOARD
          </Link>
        </div>

        <p className="mt-6 text-center label-eyebrow text-paper-low">
          ENCRYPTED // JWT // TLS 1.3
        </p>
      </motion.div>
    </div>
  );
}
