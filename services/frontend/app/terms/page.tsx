"use client";

import { motion } from "framer-motion";

const sections = [
  {
    code: "01",
    title: "서비스 소개",
    body: (
      <p>
        Quant는 AI 기반 알고리즘 트레이딩 플랫폼입니다. 시장 데이터를 자동으로 분석하고
        매매 신호를 제공하며, 사용자가 활성화한 경우 자동매매를 실행합니다.
      </p>
    ),
  },
  {
    code: "02",
    title: "투자 위험 고지",
    accent: true,
    body: (
      <>
        <p className="text-amber">⚠ 모든 투자에는 원금 손실의 위험이 있습니다.</p>
        <ul className="mt-3 space-y-2 list-none">
          <li className="flex items-baseline gap-2.5"><span className="text-amber font-mono text-[11px]">▸</span><span>암호화폐는 변동성이 매우 큰 자산이며 단기간에 큰 손실이 발생할 수 있습니다.</span></li>
          <li className="flex items-baseline gap-2.5"><span className="text-amber font-mono text-[11px]">▸</span><span>AI 분석은 과거 데이터를 기반으로 하며 미래 수익을 보장하지 않습니다.</span></li>
          <li className="flex items-baseline gap-2.5"><span className="text-amber font-mono text-[11px]">▸</span><span>자동매매 기능은 사용자의 동의 하에 작동하며, 모든 거래 결과는 사용자 책임입니다.</span></li>
          <li className="flex items-baseline gap-2.5"><span className="text-amber font-mono text-[11px]">▸</span><span>플랫폼은 시장 급변, 거래소 장애, 네트워크 문제 등으로 인한 손실을 책임지지 않습니다.</span></li>
        </ul>
      </>
    ),
  },
  {
    code: "03",
    title: "면책 조항",
    body: (
      <>
        <p>Quant는 다음 사항에 대해 책임을 지지 않습니다:</p>
        <ul className="mt-3 space-y-2 list-none">
          <li className="flex items-baseline gap-2.5"><span className="text-amber font-mono text-[11px]">▸</span><span>투자 손실, 기회 비용, 간접 손해</span></li>
          <li className="flex items-baseline gap-2.5"><span className="text-amber font-mono text-[11px]">▸</span><span>제3자(거래소, 데이터 제공자)의 서비스 중단</span></li>
          <li className="flex items-baseline gap-2.5"><span className="text-amber font-mono text-[11px]">▸</span><span>사용자가 입력한 잘못된 설정으로 인한 손실</span></li>
          <li className="flex items-baseline gap-2.5"><span className="text-amber font-mono text-[11px]">▸</span><span>해킹, 보안 사고로 인한 자산 손실 (사용자가 키 관리 책임)</span></li>
        </ul>
      </>
    ),
  },
  {
    code: "04",
    title: "사용자 의무",
    body: (
      <ul className="space-y-2 list-none">
        <li className="flex items-baseline gap-2.5"><span className="text-amber font-mono text-[11px]">▸</span><span>거래소 API 키는 사용자 본인의 자산을 위해서만 사용</span></li>
        <li className="flex items-baseline gap-2.5"><span className="text-amber font-mono text-[11px]">▸</span><span>매매 전 시장 상황과 자신의 재무 상태를 검토</span></li>
        <li className="flex items-baseline gap-2.5"><span className="text-amber font-mono text-[11px]">▸</span><span>전 재산을 한 번에 투입하지 않을 것 (분산 투자 권장)</span></li>
        <li className="flex items-baseline gap-2.5"><span className="text-amber font-mono text-[11px]">▸</span><span>불법 자금 세탁 등 부정한 목적으로 사용 금지</span></li>
      </ul>
    ),
  },
  {
    code: "05",
    title: "개인정보 처리",
    body: (
      <p>
        회원가입 시 수집하는 정보: 이메일, 비밀번호(암호화 저장).<br />
        거래소 API 키는 AES-256 GCM으로 암호화되어 저장됩니다.<br />
        개인 식별 정보는 제3자에게 제공되지 않습니다.
      </p>
    ),
  },
  {
    code: "06",
    title: "약관 변경",
    body: (
      <p>
        본 약관은 사전 공지 후 변경될 수 있습니다. 변경 후 서비스를 계속 이용하는 경우
        새 약관에 동의한 것으로 간주됩니다.
      </p>
    ),
  },
];

export default function TermsPage() {
  return (
    <div className="relative min-h-screen bg-ink overflow-hidden">
      {/* Ambient lights */}
      <div className="pointer-events-none fixed inset-0">
        <div className="bg-orb-light" style={{ top: "-100px", right: "-80px", width: "min(500px, 80vw)", height: "min(500px, 80vw)" }} />
        <div className="absolute inset-0 opacity-[0.05]" style={{
          backgroundImage: "linear-gradient(rgba(251,189,46,0.4) 1px, transparent 1px), linear-gradient(90deg, rgba(251,189,46,0.4) 1px, transparent 1px)",
          backgroundSize: "64px 64px",
        }} />
      </div>

      <main className="relative z-10 mx-auto max-w-3xl px-6 py-20">
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
        >
          <div className="flex items-baseline gap-3 mb-3">
            <span className="amber-led-static" aria-hidden />
            <p className="label-eyebrow-amber">SECTION // TERMS</p>
          </div>
          <h1 className="font-mono text-3xl sm:text-4xl font-bold tracking-tight text-paper uppercase">
            이용약관
          </h1>
          <p className="mt-2 label-eyebrow tabular">LAST_UPDATED // 2026.04</p>
        </motion.div>

        <div className="mt-12 space-y-10">
          {sections.map((s, i) => (
            <motion.section
              key={s.code}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1 + i * 0.05, duration: 0.4 }}
              className="border-l border-rule-loud pl-5"
            >
              <div className="flex items-baseline gap-3 mb-3">
                <span className="font-mono text-[10px] tracking-[0.2em] text-amber-deep tabular">{s.code}</span>
                <h2 className="font-mono text-base font-bold tracking-[0.12em] text-paper uppercase">
                  {s.title}
                </h2>
              </div>
              <div className="font-prose text-sm text-paper-dim leading-relaxed space-y-2">
                {s.body}
              </div>
            </motion.section>
          ))}
        </div>

        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.6 }}
          className="mt-12 border border-amber/30 bg-amber/[0.04] px-5 py-4"
        >
          <p className="font-prose text-xs leading-relaxed text-amber">
            본 서비스는 투자 자문이 아닙니다. 모든 매매 결정의 최종 책임은 사용자에게 있습니다.
            본 플랫폼은 정보 제공 목적으로만 운영되며, 손실에 대해 어떠한 보상도 제공하지 않습니다.
          </p>
        </motion.div>
      </main>
    </div>
  );
}
