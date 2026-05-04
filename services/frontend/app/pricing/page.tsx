"use client";

import { motion } from "framer-motion";
import Link from "next/link";

const tiers = [
  {
    code: "01",
    name: "FREE",
    price: "0",
    unit: "원",
    description: "READ_ONLY",
    features: [
      "시그널 조회 (5분 지연)",
      "대시보드 읽기 전용",
      "AI 채팅 5회/일",
      "에이전트 적중률 확인",
    ],
    highlight: false,
  },
  {
    code: "02",
    name: "PRO",
    price: "29,000",
    unit: "원/월",
    description: "AUTO_TRADING",
    features: [
      "실시간 시그널",
      "자동매매 (1자산)",
      "AI 채팅 50회/일",
      "전체 결정 이력",
      "포트폴리오 분석",
    ],
    highlight: true,
  },
  {
    code: "03",
    name: "PREMIUM",
    price: "89,000",
    unit: "원/월",
    description: "FULL_DESK",
    features: [
      "전체 자산 자동매매",
      "무제한 AI 채팅",
      "커스텀 전략",
      "우선 실행",
      "API 접근",
      "전담 지원",
    ],
    highlight: false,
  },
];

export default function PricingPage() {
  return (
    <div className="relative min-h-screen bg-ink overflow-hidden">
      {/* Ambient lights */}
      <div className="pointer-events-none fixed inset-0">
        <div className="bg-orb-light" style={{ top: "-100px", left: "-80px", width: "min(500px, 80vw)", height: "min(500px, 80vw)" }} />
        <div className="bg-orb-dim" style={{ bottom: "-80px", right: "-60px", width: "min(380px, 70vw)", height: "min(380px, 70vw)" }} />
        <div className="absolute inset-0 opacity-[0.06]" style={{
          backgroundImage: "linear-gradient(rgba(251,189,46,0.4) 1px, transparent 1px), linear-gradient(90deg, rgba(251,189,46,0.4) 1px, transparent 1px)",
          backgroundSize: "64px 64px",
        }} />
      </div>

      <main className="relative z-10 mx-auto max-w-6xl px-6 py-20">
        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="mb-12"
        >
          <div className="flex items-baseline gap-3 mb-3">
            <span className="amber-led-static" aria-hidden />
            <p className="label-eyebrow-amber">SECTION // PRICING</p>
          </div>
          <h1 className="font-mono text-3xl sm:text-4xl font-bold tracking-tight text-paper uppercase">
            플랜 선택
          </h1>
          <p className="mt-3 font-prose text-sm text-paper-dim leading-relaxed">
            AI 자동 매매 구독 — 결제 시스템 준비 중
          </p>
        </motion.div>

        {/* Tier grid */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {tiers.map((tier, i) => (
            <motion.div
              key={tier.name}
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.15 + i * 0.08, duration: 0.5 }}
              className={`relative bg-ink-50 border p-7 ${
                tier.highlight ? "border-amber panel-amber-tab" : "border-rule-loud"
              }`}
            >
              {tier.highlight && (
                <span className="absolute -top-2.5 right-5 bg-amber px-2.5 py-0.5 font-mono text-[9px] tracking-[0.2em] uppercase text-ink font-bold">
                  RECOMMENDED
                </span>
              )}

              <div className="flex items-baseline justify-between mb-5">
                <p className={`label-eyebrow ${tier.highlight ? "text-amber" : ""}`}>
                  {tier.description}
                </p>
                <p className="label-eyebrow tabular">{tier.code}/03</p>
              </div>

              <p className="font-mono text-xl font-bold tracking-[0.18em] text-paper uppercase">
                {tier.name}
              </p>

              <div className="mt-5 mb-6 pb-6 border-b border-rule">
                <div className="flex items-baseline gap-1.5">
                  <span className={`font-mono text-3xl font-bold tabular tracking-tight ${
                    tier.highlight ? "text-amber" : "text-paper"
                  }`}>
                    {tier.price}
                  </span>
                  <span className="font-prose text-sm text-paper-mute">{tier.unit}</span>
                </div>
              </div>

              <ul className="space-y-2.5">
                {tier.features.map((f) => (
                  <li key={f} className="flex items-baseline gap-2.5">
                    <span className="text-amber font-mono text-[11px] shrink-0">▸</span>
                    <span className="font-prose text-[13px] text-paper-dim leading-relaxed">{f}</span>
                  </li>
                ))}
              </ul>

              <button
                disabled
                className="mt-7 w-full py-2.5 border border-rule font-mono text-[11px] uppercase tracking-[0.16em] text-paper-mute cursor-not-allowed"
              >
                준비 중 // COMING SOON
              </button>
            </motion.div>
          ))}
        </div>

        {/* Footer note */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.5 }}
          className="mt-12 pt-8 border-t border-rule text-center"
        >
          <p className="label-eyebrow text-paper-low">
            ENCRYPTED // JWT // TLS 1.3 &middot; <Link href="/terms" className="hover:text-amber">이용약관</Link>
          </p>
        </motion.div>
      </main>
    </div>
  );
}
