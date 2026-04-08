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

interface ExecutionConfig {
  live_trading_enabled: boolean;
  allowed_exchanges: string[];
  default_shadow_mode: boolean;
}

interface UserProfile {
  email: string;
  plan?: string;
  roles?: string[];
}

/* ── AI Provider OAuth Card ── */
function AiProviderCard({ provider, name, description, color, iconColor }: {
  provider: string; name: string; description: string; color: string; iconColor: string;
}) {
  const [status, setStatus] = useState<"loading" | "connected" | "disconnected">("loading");

  useEffect(() => {
    gatewayFetch(`/auth/${provider}/status`)
      .then((d) => setStatus(d.authenticated ? "connected" : "disconnected"))
      .catch(() => setStatus("disconnected"));
  }, [provider]);

  const handleConnect = async () => {
    try {
      const data = await gatewayFetch(`/auth/${provider}/login`);
      if (data.auth_url) {
        window.open(data.auth_url, "_blank", "width=600,height=700");
      }
    } catch {
      alert("연동 URL을 가져올 수 없습니다");
    }
  };

  // Check URL params for OAuth callback result
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("oauth") === provider && params.get("status") === "success") {
      setStatus("connected");
      window.history.replaceState({}, "", "/settings");
    }
  }, [provider]);

  return (
    <div className={`rounded-xl border p-4 ${color}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className={`flex h-10 w-10 items-center justify-center rounded-lg bg-white/10 text-lg ${iconColor}`}>
            {provider === "claude" ? "🤖" : "⚡"}
          </div>
          <div>
            <p className="text-sm font-medium text-zinc-200">{name}</p>
            <p className="text-[11px] text-zinc-500">{description}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {status === "loading" ? (
            <span className="text-xs text-neutral-500">확인 중...</span>
          ) : status === "connected" ? (
            <span className="flex items-center gap-1.5 rounded-full bg-emerald-500/20 px-3 py-1 text-xs font-medium text-emerald-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              연동됨
            </span>
          ) : (
            <button
              onClick={handleConnect}
              className="rounded-lg bg-white px-4 py-1.5 text-xs font-semibold text-black transition-all hover:shadow-[0_0_20px_rgba(255,255,255,0.15)] active:scale-95"
            >
              연동하기
            </button>
          )}
        </div>
      </div>
    </div>
  );
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
  const [execConfig, setExecConfig] = useState<ExecutionConfig | null>(null);
  const [execLoading, setExecLoading] = useState(true);

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

  const fetchExecConfig = useCallback(() => {
    setExecLoading(true);
    gatewayFetch("/admin/execution/config")
      .then((data) => setExecConfig(data as ExecutionConfig))
      .catch(() => setExecConfig(null))
      .finally(() => setExecLoading(false));
  }, []);

  const isAdmin = profile?.roles?.includes("admin") ?? false;

  async function updateExecConfig(patch: Partial<ExecutionConfig>) {
    if (!execConfig) return;
    const updated = { ...execConfig, ...patch };
    setExecConfig(updated);
    try {
      await gatewayFetch("/admin/execution/config", {
        method: "PUT",
        body: JSON.stringify(updated),
      });
    } catch {
      fetchExecConfig();
    }
  }

  useEffect(() => {
    fetchCredentials();
    fetchRisk();
    fetchExecConfig();
    const claims = readTokenClaims();
    if (claims) {
      setProfile({ email: claims.email ?? claims.sub ?? "", roles: claims.roles });
    }
  }, [fetchCredentials, fetchRisk, fetchExecConfig]);

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
          <h2 className="text-2xl font-semibold tracking-tight text-zinc-50">설정</h2>
          <p className="mt-1 text-sm text-zinc-400 leading-relaxed">
            거래소를 연결하고, AI 자동 매매를 설정해보세요
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
                  <span className="text-sm">{"\uD83D\uDE80"}</span>
                </div>
                <div>
                  <p className="text-sm font-medium text-zinc-200">처음이신가요? 시작 가이드</p>
                  <p className="text-xs text-zinc-500">3단계만 따라하면 AI 자동 매매를 시작할 수 있어요</p>
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
                        { step: "1", icon: "\uD83D\uDD17", title: "거래소 연결하기", desc: "아래에서 거래소를 선택하고 API 키를 등록해주세요. 거래소에서 발급받은 키를 복사해서 붙여넣기만 하면 돼요." },
                        { step: "2", icon: "\uD83E\uDD16", title: "AI 연결하기", desc: "아래 AI 연동 섹션에서 연결 버튼을 누르세요. 클릭 한 번이면 끝나요." },
                        { step: "3", icon: "\u2705", title: "자동 매매 시작!", desc: "끝! AI가 알아서 시장을 분석하고 좋은 기회에 자동으로 매매해요. 대시보드에서 확인하세요." },
                      ].map((item) => (
                        <div key={item.step} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                          <div className="mb-2 text-2xl">{item.icon}</div>
                          <div className="mb-1 flex h-6 w-6 items-center justify-center rounded-full bg-white text-xs font-bold text-black">
                            {item.step}
                          </div>
                          <p className="text-sm font-medium text-zinc-200">{item.title}</p>
                          <p className="mt-1 text-sm text-zinc-400 leading-relaxed">{item.desc}</p>
                        </div>
                      ))}
                    </div>

                    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                      <p className="mb-2 text-xs font-medium text-zinc-400">거래소별 연결 방법</p>
                      {EXCHANGES.map((ex) => (
                        <div key={ex.id} className="flex items-start gap-2 py-1.5">
                          <span className="mt-0.5 text-xs font-semibold text-zinc-50">{ex.name}</span>
                          <span className="text-xs text-zinc-500">{ex.guide}</span>
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
                <h3 className="text-base font-semibold tracking-tight text-zinc-50">거래소 연결하기</h3>
                <p className="mt-0.5 text-xs text-neutral-500">거래소에서 발급받은 키를 입력하면 AI가 자동으로 매매할 수 있어요</p>
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
                  <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-zinc-500">거래소</label>
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
                    <p className="mt-1.5 text-[11px] leading-relaxed text-neutral-500">
                      {currentExchange.guide}
                    </p>
                  )}
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-zinc-500">API 키</label>
                  <input
                    className="input-field"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="발급받은 API 키 입력"
                    type="password"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-zinc-500">API 시크릿</label>
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
                <p className="mb-3 text-[11px] font-medium uppercase tracking-wider text-zinc-500">연결된 거래소</p>
                {credLoading ? (
                  <div className="space-y-2">
                    {[0, 1].map((i) => (
                      <div key={i} className="skeleton h-14 rounded-xl" />
                    ))}
                  </div>
                ) : credentials.length === 0 ? (
                  <div className="text-center py-4">
                    <p className="text-xl mb-2">{"\uD83D\uDD0C"}</p>
                    <p className="text-sm text-neutral-500">아직 연결된 거래소가 없어요</p>
                    <p className="text-xs text-neutral-600 mt-1">위에서 거래소를 선택하고 키를 입력해보세요</p>
                  </div>
                ) : (
                  <StaggerContainer className="space-y-2">
                    {credentials.map((cred) => (
                      <StaggerItem key={cred.credential_id}>
                        <div className="flex items-center justify-between rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
                          <div>
                            <span className="text-sm font-medium text-zinc-200">
                              {EXCHANGES.find((e) => e.id === cred.exchange)?.name || cred.exchange}
                            </span>
                            <p className="mt-0.5 font-mono text-[11px] text-neutral-500">{cred.api_key_masked}</p>
                            {cred.sandbox && (
                              <span className="text-[10px] text-neutral-500">테스트 모드</span>
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
                <h3 className="text-base font-semibold tracking-tight text-zinc-50">리스크 관리</h3>
                {riskLoading ? (
                  <div className="grid grid-cols-2 gap-3">
                    {[0, 1, 2, 3].map((i) => (
                      <div key={i} className="skeleton h-20 rounded-xl" />
                    ))}
                  </div>
                ) : risk ? (
                  <div className="grid grid-cols-2 gap-3">
                    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                      <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">최대 주문금액</p>
                      <p className="mt-2 font-mono text-xl font-semibold tabular-nums text-zinc-50">
                        {risk.max_notional != null ? (
                          <><span className="text-sm text-neutral-500">$</span><AnimatedNumber value={risk.max_notional} decimals={0} /></>
                        ) : "--"}
                      </p>
                    </div>
                    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                      <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">투자 한도</p>
                      <p className="mt-2 font-mono text-xl font-semibold tabular-nums text-zinc-50">
                        {risk.exposure_limit != null ? (
                          <><span className="text-sm text-neutral-500">$</span><AnimatedNumber value={risk.exposure_limit} decimals={0} /></>
                        ) : "--"}
                      </p>
                    </div>
                    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                      <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">최대 하락 허용</p>
                      <p className="mt-2 font-mono text-xl font-semibold tabular-nums text-zinc-50">
                        {risk.max_drawdown != null ? (
                          <><AnimatedNumber value={risk.max_drawdown * 100} decimals={1} /><span className="text-sm text-neutral-500">%</span></>
                        ) : "--"}
                      </p>
                    </div>
                    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                      <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">자동 매매</p>
                      <p className="mt-2 text-xl font-semibold">
                        <span className={risk.automation_enabled ? "text-emerald-400" : "text-red-400"}>
                          {risk.automation_enabled ? "활성" : "비활성"}
                        </span>
                      </p>
                    </div>
                  </div>
                ) : (
                  <p className="text-sm text-neutral-500">리스크 설정을 불러올 수 없습니다</p>
                )}
              </section>
            </FadeInView>

            {/* Execution Config */}
            <FadeInView delay={0.12}>
              <section className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-6 space-y-4">
                <h3 className="text-base font-semibold tracking-tight text-zinc-50">실행 설정</h3>
                {execLoading ? (
                  <div className="space-y-2">
                    {[0, 1, 2].map((i) => (
                      <div key={i} className="skeleton h-14 rounded-xl" />
                    ))}
                  </div>
                ) : execConfig ? (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                      <div>
                        <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">실시간 트레이딩</p>
                        <p className={`mt-1 text-sm font-semibold ${execConfig.live_trading_enabled ? "text-emerald-400" : "text-red-400"}`}>
                          {execConfig.live_trading_enabled ? "활성" : "비활성"}
                        </p>
                      </div>
                      {isAdmin && (
                        <button
                          className={`relative h-6 w-11 rounded-full transition-colors ${execConfig.live_trading_enabled ? "bg-emerald-500" : "bg-neutral-700"}`}
                          onClick={() => updateExecConfig({ live_trading_enabled: !execConfig.live_trading_enabled })}
                        >
                          <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-transform ${execConfig.live_trading_enabled ? "left-[22px]" : "left-0.5"}`} />
                        </button>
                      )}
                    </div>
                    <div className="flex items-center justify-between rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                      <div>
                        <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">섀도우 모드</p>
                        <p className={`mt-1 text-sm font-semibold ${execConfig.default_shadow_mode ? "text-zinc-50" : "text-zinc-400"}`}>
                          {execConfig.default_shadow_mode ? "기본 활성" : "비활성"}
                        </p>
                      </div>
                      {isAdmin && (
                        <button
                          className={`relative h-6 w-11 rounded-full transition-colors ${execConfig.default_shadow_mode ? "bg-white" : "bg-neutral-700"}`}
                          onClick={() => updateExecConfig({ default_shadow_mode: !execConfig.default_shadow_mode })}
                        >
                          <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-transform ${execConfig.default_shadow_mode ? "left-[22px]" : "left-0.5"}`} />
                        </button>
                      )}
                    </div>
                    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                      <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">허용 거래소</p>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {EXCHANGES.map((ex) => {
                          const enabled = execConfig.allowed_exchanges.map((e) => e.toLowerCase()).includes(ex.id);
                          return (
                            <button
                              key={ex.id}
                              disabled={!isAdmin}
                              onClick={() => {
                                if (!isAdmin) return;
                                const updated = enabled
                                  ? execConfig.allowed_exchanges.filter((e) => e.toLowerCase() !== ex.id)
                                  : [...execConfig.allowed_exchanges, ex.id];
                                updateExecConfig({ allowed_exchanges: updated });
                              }}
                              className={`rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors ${
                                enabled
                                  ? "bg-white/10 text-neutral-300"
                                  : "bg-white/[0.03] text-neutral-500"
                              } ${isAdmin ? "cursor-pointer hover:bg-white/20" : ""}`}
                            >
                              {ex.name}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                    {isAdmin && (
                      <p className="text-[10px] text-neutral-500">
                        관리자 권한으로 위 설정을 직접 변경할 수 있습니다
                      </p>
                    )}
                  </div>
                ) : (
                  <p className="text-sm text-neutral-500">실행 설정을 불러올 수 없습니다</p>
                )}
              </section>
            </FadeInView>

            {/* 구독 플랜 */}
            <FadeInView delay={0.12}>
              <section className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-6 space-y-4">
                <div>
                  <h3 className="text-base font-semibold tracking-tight text-zinc-50">구독 플랜</h3>
                  <p className="mt-0.5 text-xs text-zinc-500">현재 플랜과 사용량을 확인하세요</p>
                </div>

                {/* Current tier display */}
                <div className="rounded-xl border border-white/[0.08] bg-white/[0.04] p-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-[10px] font-medium uppercase tracking-wider text-zinc-500">현재 플랜</p>
                      <p className="mt-1 text-lg font-bold text-zinc-50">{profile?.plan || "FREE"}</p>
                    </div>
                    <span className="rounded-full bg-white/[0.08] px-3 py-1 text-xs text-zinc-400">
                      {profile?.plan === "PREMIUM" ? "모든 기능" : profile?.plan === "PRO" ? "자동매매 1자산" : "시그널 조회"}
                    </span>
                  </div>
                </div>

                {/* Tier comparison */}
                <div className="grid grid-cols-3 gap-3">
                  {[
                    { name: "Free", price: "0원", features: ["시그널 조회 (지연)", "대시보드", "AI 채팅 5회/일"] },
                    { name: "Pro", price: "29,000원/월", features: ["실시간 시그널", "자동매매 (1자산)", "AI 채팅 50회/일"] },
                    { name: "Premium", price: "89,000원/월", features: ["전체 자산", "무제한 자동매매", "커스텀 전략", "우선 실행"] },
                  ].map(tier => (
                    <div key={tier.name} className={`rounded-xl border p-4 ${profile?.plan === tier.name.toUpperCase() ? "border-white/[0.20] bg-white/[0.06]" : "border-white/[0.06] bg-white/[0.02]"}`}>
                      <p className="text-sm font-medium text-zinc-200">{tier.name}</p>
                      <p className="mt-1 text-lg font-bold text-zinc-50">{tier.price}</p>
                      <ul className="mt-3 space-y-1">
                        {tier.features.map(f => (
                          <li key={f} className="text-[11px] text-zinc-400">• {f}</li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              </section>
            </FadeInView>

            {/* 고급: 자체 LLM 연동 (선택사항) */}
            <FadeInView delay={0.14}>
              <section className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-6 space-y-4">
                <div>
                  <h3 className="text-base font-semibold tracking-tight text-zinc-50">AI 연동 (선택사항)</h3>
                  <p className="mt-0.5 text-sm text-zinc-400 leading-relaxed">더 똑똑한 AI를 사용하고 싶다면 연동해보세요. 연동하지 않아도 기본 AI가 제공돼요.</p>
                </div>
                <AiProviderCard
                  provider="claude"
                  name="Claude (Anthropic)"
                  description="Claude Sonnet/Opus로 시장 분석, 매매 판단, 도구 호출"
                  color="border-white/[0.08] bg-white/[0.03]"
                  iconColor="text-white"
                />
                <AiProviderCard
                  provider="codex"
                  name="Codex (OpenAI)"
                  description="GPT-4o로 시장 분석, 매매 판단, 도구 호출"
                  color="border-white/[0.08] bg-white/[0.03]"
                  iconColor="text-white"
                />
                <p className="text-[10px] text-neutral-500">
                  OAuth PKCE로 안전하게 연동됩니다. API 키가 서버에 저장되지 않습니다.
                </p>
              </section>
            </FadeInView>

            {/* Profile */}
            <FadeInView delay={0.15}>
              <section className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-6 space-y-4">
                <h3 className="text-base font-semibold tracking-tight text-zinc-50">내 계정</h3>
                {profile ? (
                  <div className="space-y-3">
                    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                      <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">이메일</p>
                      <p className="mt-1 text-sm text-zinc-50">{profile.email}</p>
                    </div>
                    {profile.roles && profile.roles.length > 0 && (
                      <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                        <p className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">권한</p>
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
                  <p className="text-sm text-neutral-500">계정 정보를 불러올 수 없습니다</p>
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
