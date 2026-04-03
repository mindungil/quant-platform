"use client";

import { useEffect, useState, useCallback } from "react";
import { gatewayFetch, readTokenClaims } from "../../lib/api";
import { AuthGuard } from "../../components/auth-guard";
import {
  PageTransition,
  FadeInView,
  StaggerContainer,
  StaggerItem,
  AnimatePresence,
  motion,
  AnimatedNumber,
} from "../../components/motion";

interface Credential {
  credential_id: string;
  exchange: string;
  api_key_masked: string;
  created_at?: string;
  sandbox?: boolean;
}

interface RiskSettings {
  max_notional?: number;
  exposure_limit?: number;
  max_drawdown?: number;
  automation_enabled?: boolean;
  [key: string]: unknown;
}

interface UserProfile {
  email: string;
  plan?: string;
  roles?: string[];
}

const EXCHANGES: { id: string; name: string; guide: string }[] = [
  {
    id: "binance",
    name: "Binance",
    guide: "binance.com → 프로필 → API 관리 → API 키 생성 → IP 제한 설정 권장",
  },
  {
    id: "upbit",
    name: "Upbit",
    guide: "upbit.com → 마이페이지 → Open API 관리 → API 키 발급",
  },
  {
    id: "alpaca",
    name: "Alpaca",
    guide: "app.alpaca.markets → Paper Trading → API Keys 에서 발급",
  },
];

function SettingsContent() {
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [credLoading, setCredLoading] = useState(true);
  const [exchange, setExchange] = useState("binance");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [credSaving, setCredSaving] = useState(false);
  const [credError, setCredError] = useState<string | null>(null);
  const [credSuccess, setCredSuccess] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [risk, setRisk] = useState<RiskSettings | null>(null);
  const [riskLoading, setRiskLoading] = useState(true);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [showGuide, setShowGuide] = useState(false);

  const fetchCredentials = useCallback(() => {
    setCredLoading(true);
    gatewayFetch("/settings/credentials")
      .then((data) => {
        const items = Array.isArray(data) ? data : (data as { credentials?: Credential[] }).credentials ?? [];
        setCredentials(items as Credential[]);
      })
      .catch(() => setCredentials([]))
      .finally(() => setCredLoading(false));
  }, []);

  const fetchRisk = useCallback(() => {
    setRiskLoading(true);
    gatewayFetch("/settings/risk")
      .then((data) => setRisk(data as RiskSettings))
      .catch(() => setRisk(null))
      .finally(() => setRiskLoading(false));
  }, []);

  useEffect(() => {
    fetchCredentials();
    fetchRisk();
    const claims = readTokenClaims();
    if (claims) {
      setProfile({ email: claims.email ?? claims.sub ?? "", roles: claims.roles });
    }
  }, [fetchCredentials, fetchRisk]);

  async function saveCredential() {
    if (!apiKey.trim() || !apiSecret.trim()) {
      setCredError("API 키와 시크릿을 입력하세요");
      return;
    }
    setCredError(null);
    setCredSuccess(null);
    setCredSaving(true);
    try {
      await gatewayFetch("/settings/credentials", {
        method: "POST",
        body: JSON.stringify({ exchange, api_key: apiKey.trim(), api_secret: apiSecret.trim(), sandbox: true }),
      });
      setCredSuccess("거래소 연결 완료");
      setApiKey("");
      setApiSecret("");
      fetchCredentials();
    } catch (e) {
      setCredError(e instanceof Error ? e.message : "저장 실패");
    } finally {
      setCredSaving(false);
    }
  }

  async function deleteCredential(credentialId: string, ex: string) {
    setDeletingId(credentialId);
    try {
      await gatewayFetch(`/settings/credentials/${ex}`, { method: "DELETE" });
      fetchCredentials();
    } catch {
      /* ignore */
    } finally {
      setDeletingId(null);
    }
  }

  const currentExchange = EXCHANGES.find((e) => e.id === exchange);

  return (
    <PageTransition>
      <main className="grid gap-6">
        {/* Header */}
        <div>
          <h2 className="text-2xl font-semibold text-white">설정</h2>
          <p className="mt-1 text-sm text-neutral-500">
            거래소 연결, 리스크 관리, 계정 정보
          </p>
        </div>

        {/* Quick start guide */}
        <FadeInView>
          <motion.div
            className="cursor-pointer rounded-2xl border border-white/[0.06] bg-white/[0.03] p-5"
            onClick={() => setShowGuide(!showGuide)}
            whileHover={{ borderColor: "rgba(255,255,255,0.12)" }}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/10">
                  <span className="text-sm">?</span>
                </div>
                <div>
                  <p className="text-sm font-medium text-white">시작 가이드</p>
                  <p className="text-xs text-neutral-500">플랫폼 사용법과 거래소 연결 방법</p>
                </div>
              </div>
              <motion.span
                className="text-neutral-500"
                animate={{ rotate: showGuide ? 180 : 0 }}
              >
                ▼
              </motion.span>
            </div>

            <AnimatePresence>
              {showGuide && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.3 }}
                  style={{ overflow: "hidden" }}
                  onClick={(e) => e.stopPropagation()}
                >
                  <div className="mt-4 space-y-4 border-t border-white/[0.06] pt-4">
                    <div className="grid gap-3 md:grid-cols-3">
                      {[
                        { step: "1", title: "거래소 API 발급", desc: "아래에서 거래소를 선택하고 API 키를 등록하세요. 읽기+거래 권한이 필요합니다." },
                        { step: "2", title: "AI 에이전트 연결", desc: "AI 에이전트 탭에서 Claude 또는 Codex 계정을 OAuth로 연결하세요." },
                        { step: "3", title: "자동 매매 시작", desc: "에이전트가 시장을 분석하고 자동으로 매매합니다. 대시보드에서 모니터링하세요." },
                      ].map((item) => (
                        <div key={item.step} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                          <div className="mb-2 flex h-6 w-6 items-center justify-center rounded-full bg-white text-xs font-bold text-black">
                            {item.step}
                          </div>
                          <p className="text-sm font-medium text-white">{item.title}</p>
                          <p className="mt-1 text-xs leading-relaxed text-neutral-500">{item.desc}</p>
                        </div>
                      ))}
                    </div>

                    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                      <p className="mb-2 text-xs font-medium text-neutral-400">거래소별 API 발급 방법</p>
                      {EXCHANGES.map((ex) => (
                        <div key={ex.id} className="flex items-start gap-2 py-1.5">
                          <span className="mt-0.5 text-xs font-semibold text-white">{ex.name}</span>
                          <span className="text-xs text-neutral-500">{ex.guide}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </motion.div>
        </FadeInView>

        <div className="grid gap-6 lg:grid-cols-2">
          {/* API Credentials */}
          <FadeInView delay={0.05}>
            <section className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-6 space-y-5">
              <div>
                <h3 className="text-lg font-semibold text-white">거래소 연결</h3>
                <p className="mt-0.5 text-xs text-neutral-500">거래소 API 키를 등록하면 자동 매매가 가능합니다</p>
              </div>

              <AnimatePresence mode="wait">
                {credError && (
                  <motion.p
                    key="err"
                    initial={{ opacity: 0, y: -4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    className="rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-400"
                  >
                    {credError}
                  </motion.p>
                )}
                {credSuccess && (
                  <motion.p
                    key="ok"
                    initial={{ opacity: 0, y: -4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    className="rounded-lg bg-emerald-500/10 px-3 py-2 text-xs text-emerald-400"
                  >
                    {credSuccess}
                  </motion.p>
                )}
              </AnimatePresence>

              <div className="space-y-3">
                <div>
                  <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-widest text-neutral-500">거래소</label>
                  <select
                    className="input-field"
                    value={exchange}
                    onChange={(e) => setExchange(e.target.value)}
                  >
                    {EXCHANGES.map((ex) => (
                      <option key={ex.id} value={ex.id}>{ex.name}</option>
                    ))}
                  </select>
                  {currentExchange && (
                    <p className="mt-1.5 text-[11px] leading-relaxed text-neutral-600">
                      {currentExchange.guide}
                    </p>
                  )}
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-widest text-neutral-500">API 키</label>
                  <input
                    className="input-field"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="발급받은 API 키 입력"
                    type="password"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-widest text-neutral-500">API 시크릿</label>
                  <input
                    className="input-field"
                    value={apiSecret}
                    onChange={(e) => setApiSecret(e.target.value)}
                    placeholder="발급받은 시크릿 키 입력"
                    type="password"
                  />
                </div>
                <motion.button
                  onClick={saveCredential}
                  disabled={credSaving || !apiKey || !apiSecret}
                  className="btn-primary w-full disabled:opacity-30"
                  whileTap={{ scale: 0.98 }}
                >
                  {credSaving ? "연결 중..." : "거래소 연결"}
                </motion.button>
              </div>

              {/* Saved credentials */}
              <div className="border-t border-white/[0.06] pt-4">
                <p className="mb-3 text-[11px] font-medium uppercase tracking-widest text-neutral-500">연결된 거래소</p>
                {credLoading ? (
                  <div className="space-y-2">
                    {[0, 1].map((i) => (
                      <div key={i} className="skeleton h-14 rounded-xl" />
                    ))}
                  </div>
                ) : credentials.length === 0 ? (
                  <p className="text-sm text-neutral-600">아직 연결된 거래소가 없습니다</p>
                ) : (
                  <StaggerContainer className="space-y-2">
                    {credentials.map((cred) => (
                      <StaggerItem key={cred.credential_id}>
                        <div className="flex items-center justify-between rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
                          <div>
                            <span className="text-sm font-medium text-white">
                              {EXCHANGES.find((e) => e.id === cred.exchange)?.name || cred.exchange}
                            </span>
                            <p className="mt-0.5 font-mono text-[11px] text-neutral-600">{cred.api_key_masked}</p>
                            {cred.sandbox && (
                              <span className="text-[10px] text-neutral-600">테스트 모드</span>
                            )}
                          </div>
                          <button
                            onClick={() => deleteCredential(cred.credential_id, cred.exchange)}
                            disabled={deletingId === cred.credential_id}
                            className="rounded-lg border border-white/10 px-3 py-1 text-xs text-neutral-500 transition-colors hover:border-red-500/30 hover:text-red-400 disabled:opacity-30"
                          >
                            {deletingId === cred.credential_id ? "..." : "해제"}
                          </button>
                        </div>
                      </StaggerItem>
                    ))}
                  </StaggerContainer>
                )}
              </div>
            </section>
          </FadeInView>

          <div className="space-y-6">
            {/* Risk Settings */}
            <FadeInView delay={0.1}>
              <section className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-6 space-y-4">
                <h3 className="text-lg font-semibold text-white">리스크 관리</h3>
                {riskLoading ? (
                  <div className="grid grid-cols-2 gap-3">
                    {[0, 1, 2, 3].map((i) => (
                      <div key={i} className="skeleton h-20 rounded-xl" />
                    ))}
                  </div>
                ) : risk ? (
                  <div className="grid grid-cols-2 gap-3">
                    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                      <p className="text-[11px] font-medium uppercase tracking-widest text-neutral-500">최대 주문금액</p>
                      <p className="mt-2 font-mono text-xl font-semibold text-white">
                        {risk.max_notional != null ? (
                          <><span className="text-sm text-neutral-500">$</span><AnimatedNumber value={risk.max_notional} decimals={0} /></>
                        ) : "--"}
                      </p>
                    </div>
                    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                      <p className="text-[11px] font-medium uppercase tracking-widest text-neutral-500">투자 한도</p>
                      <p className="mt-2 font-mono text-xl font-semibold text-white">
                        {risk.exposure_limit != null ? (
                          <><span className="text-sm text-neutral-500">$</span><AnimatedNumber value={risk.exposure_limit} decimals={0} /></>
                        ) : "--"}
                      </p>
                    </div>
                    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                      <p className="text-[11px] font-medium uppercase tracking-widest text-neutral-500">최대 하락 허용</p>
                      <p className="mt-2 font-mono text-xl font-semibold text-white">
                        {risk.max_drawdown != null ? (
                          <><AnimatedNumber value={risk.max_drawdown * 100} decimals={1} /><span className="text-sm text-neutral-500">%</span></>
                        ) : "--"}
                      </p>
                    </div>
                    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                      <p className="text-[11px] font-medium uppercase tracking-widest text-neutral-500">자동 매매</p>
                      <p className="mt-2 text-xl font-semibold">
                        <span className={risk.automation_enabled ? "text-emerald-400" : "text-red-400"}>
                          {risk.automation_enabled ? "활성" : "비활성"}
                        </span>
                      </p>
                    </div>
                  </div>
                ) : (
                  <p className="text-sm text-neutral-600">리스크 설정을 불러올 수 없습니다</p>
                )}
              </section>
            </FadeInView>

            {/* Profile */}
            <FadeInView delay={0.15}>
              <section className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-6 space-y-4">
                <h3 className="text-lg font-semibold text-white">내 계정</h3>
                {profile ? (
                  <div className="space-y-3">
                    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                      <p className="text-[11px] font-medium uppercase tracking-widest text-neutral-500">이메일</p>
                      <p className="mt-1 text-sm text-white">{profile.email}</p>
                    </div>
                    {profile.roles && profile.roles.length > 0 && (
                      <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                        <p className="text-[11px] font-medium uppercase tracking-widest text-neutral-500">권한</p>
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {profile.roles.map((role) => (
                            <span
                              key={role}
                              className="rounded-full bg-white/10 px-2.5 py-0.5 text-xs font-medium text-neutral-300"
                            >
                              {role === "admin" ? "관리자" : role === "user" ? "사용자" : role}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <p className="text-sm text-neutral-600">계정 정보를 불러올 수 없습니다</p>
                )}
              </section>
            </FadeInView>
          </div>
        </div>
      </main>
    </PageTransition>
  );
}

export default function SettingsPage() {
  return (
    <AuthGuard>
      <SettingsContent />
    </AuthGuard>
  );
}
