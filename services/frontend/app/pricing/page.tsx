"use client";
import { motion } from "framer-motion";

const tiers = [
  {
    name: "Free",
    price: "0",
    unit: "원",
    description: "시작하기",
    features: [
      "시그널 조회 (5분 지연)",
      "대시보드 읽기 전용",
      "AI 채팅 5회/일",
      "에이전트 적중률 확인",
    ],
    cta: "무료로 시작",
    highlight: false,
  },
  {
    name: "Pro",
    price: "29,000",
    unit: "원/월",
    description: "자동매매 시작",
    features: [
      "실시간 시그널",
      "자동매매 (1자산)",
      "AI 채팅 50회/일",
      "전체 결정 이력",
      "포트폴리오 분석",
    ],
    cta: "Pro 시작하기",
    highlight: true,
  },
  {
    name: "Premium",
    price: "89,000",
    unit: "원/월",
    description: "전문 트레이더",
    features: [
      "전체 자산 자동매매",
      "무제한 AI 채팅",
      "커스텀 전략",
      "우선 실행",
      "API 접근",
      "전담 지원",
    ],
    cta: "Premium 시작하기",
    highlight: false,
  },
];

export default function PricingPage() {
  return (
    <main className="min-h-screen bg-zinc-950 px-6 py-20">
      <div className="mx-auto max-w-5xl text-center">
        <motion.h1
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          className="text-2xl font-semibold tracking-tight text-zinc-50"
        >
          플랜 선택
        </motion.h1>
        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.2 }}
          className="mt-3 text-sm text-zinc-400 leading-relaxed"
        >
          AI 에이전트가 24시간 시장을 분석합니다. 당신은 구독만 하세요.
        </motion.p>

        <div className="mt-16 grid grid-cols-1 gap-6 md:grid-cols-3">
          {tiers.map((tier, i) => (
            <motion.div
              key={tier.name}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1 * (i + 1) }}
              className={`rounded-2xl border p-8 text-left hover-lift ${
                tier.highlight
                  ? "border-white/[0.20] bg-white/[0.06]"
                  : "border-white/[0.06] bg-white/[0.03]"
              }`}
            >
              {tier.highlight && (
                <span className="mb-4 inline-block rounded-full bg-white px-3 py-1 text-[10px] font-bold uppercase tracking-wider text-black">
                  추천
                </span>
              )}
              <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">{tier.description}</p>
              <p className="mt-2 text-3xl font-bold tracking-tighter tabular-nums text-zinc-50">
                {tier.price}
                <span className="text-sm font-normal text-zinc-500">{tier.unit}</span>
              </p>
              <ul className="mt-6 space-y-3">
                {tier.features.map((f) => (
                  <li key={f} className="flex items-center gap-2 text-sm text-zinc-400 leading-relaxed">
                    <span className="text-green-500">&#10003;</span>
                    {f}
                  </li>
                ))}
              </ul>
              <button
                className={`mt-8 w-full rounded-lg py-2.5 text-sm font-semibold transition-all ${
                  tier.highlight
                    ? "bg-white text-black hover:bg-zinc-200"
                    : "border border-white/[0.10] text-zinc-50 hover:bg-white/[0.06]"
                }`}
              >
                {tier.cta}
              </button>
            </motion.div>
          ))}
        </div>
      </div>
    </main>
  );
}
