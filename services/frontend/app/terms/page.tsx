"use client";
import { motion } from "framer-motion";

export default function TermsPage() {
  return (
    <main className="min-h-screen bg-zinc-950 px-6 py-16">
      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
        className="mx-auto max-w-3xl">
        <h1 className="text-3xl font-bold text-white text-glow">이용약관</h1>
        <p className="mt-2 text-zinc-500">최종 수정: 2026년 4월</p>

        <div className="mt-10 space-y-8 text-sm leading-relaxed text-zinc-300">
          <section>
            <h2 className="text-lg font-semibold text-white">1. 서비스 소개</h2>
            <p className="mt-2">
              Quant는 AI 기반 알고리즘 트레이딩 플랫폼입니다. 시장 데이터를 자동으로 분석하고
              매매 신호를 제공하며, 사용자가 활성화한 경우 자동매매를 실행합니다.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white">2. 투자 위험 고지</h2>
            <p className="mt-2 text-amber-400">
              ⚠️ 모든 투자에는 원금 손실의 위험이 있습니다.
            </p>
            <ul className="mt-3 space-y-2 list-disc pl-5">
              <li>암호화폐는 변동성이 매우 큰 자산이며 단기간에 큰 손실이 발생할 수 있습니다.</li>
              <li>AI 분석은 과거 데이터를 기반으로 하며 미래 수익을 보장하지 않습니다.</li>
              <li>자동매매 기능은 사용자의 동의 하에 작동하며, 모든 거래 결과는 사용자 책임입니다.</li>
              <li>플랫폼은 시장 급변, 거래소 장애, 네트워크 문제 등으로 인한 손실을 책임지지 않습니다.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white">3. 면책 조항</h2>
            <p className="mt-2">
              Quant는 다음 사항에 대해 책임을 지지 않습니다:
            </p>
            <ul className="mt-3 space-y-2 list-disc pl-5">
              <li>투자 손실, 기회 비용, 간접 손해</li>
              <li>제3자(거래소, 데이터 제공자)의 서비스 중단</li>
              <li>사용자가 입력한 잘못된 설정으로 인한 손실</li>
              <li>해킹, 보안 사고로 인한 자산 손실 (사용자가 키 관리 책임)</li>
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white">4. 사용자 의무</h2>
            <ul className="mt-3 space-y-2 list-disc pl-5">
              <li>거래소 API 키는 사용자 본인의 자산을 위해서만 사용</li>
              <li>매매 전 시장 상황과 자신의 재무 상태를 검토</li>
              <li>전 재산을 한 번에 투입하지 않을 것 (분산 투자 권장)</li>
              <li>불법 자금 세탁 등 부정한 목적으로 사용 금지</li>
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white">5. 개인정보 처리</h2>
            <p className="mt-2">
              회원가입 시 수집하는 정보: 이메일, 비밀번호(암호화 저장)<br/>
              거래소 API 키는 AES-256 GCM으로 암호화되어 저장됩니다.<br/>
              개인 식별 정보는 제3자에게 제공되지 않습니다.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white">6. 약관 변경</h2>
            <p className="mt-2">
              본 약관은 사전 공지 후 변경될 수 있습니다. 변경 후 서비스를 계속 이용하는 경우
              새 약관에 동의한 것으로 간주됩니다.
            </p>
          </section>

          <section className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-4">
            <p className="text-xs text-amber-400">
              본 서비스는 투자 자문이 아닙니다. 모든 매매 결정의 최종 책임은 사용자에게 있습니다.
              본 플랫폼은 정보 제공 목적으로만 운영되며, 손실에 대해 어떠한 보상도 제공하지 않습니다.
            </p>
          </section>
        </div>
      </motion.div>
    </main>
  );
}
