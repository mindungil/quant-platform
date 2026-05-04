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

interface LaneAllocation {
  user_id: string;
  asset_type: string;
  agent_pct: number;
  template_pct: number;
}

/* ── Lane Allocation Slider Section ── */
function LaneAllocationSection() {
  const [alloc, setAlloc] = useState<LaneAllocation | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    gatewayFetch("/settings/lane-allocation?asset_type=crypto")
      .then((d) => setAlloc(d as LaneAllocation))
      .catch(async () => {
        // Try system-wide defaults endpoint first — honest fallback over arbitrary 70/30
        try {
          const r = await gatewayFetch("/config/allocation-defaults");
          const d = r as { agent_pct?: number; template_pct?: number };
          setAlloc({
            user_id: "",
            asset_type: "crypto",
            agent_pct: d.agent_pct ?? 0.5,
            template_pct: d.template_pct ?? 0.5,
          });
          return;
        } catch {
          /* fall through to neutral default */
        }
        // True empty state: 50/50 (no preference) — not 70/30 which pretends to be informed
        setAlloc({ user_id: "", asset_type: "crypto", agent_pct: 0.5, template_pct: 0.5 });
      })
      .finally(() => setLoading(false));
  }, []);

  const save = async (next: { agent_pct: number; template_pct: number }) => {
    if (!alloc) return;
    setSaving(true);
    try {
      const updated = await gatewayFetch("/settings/lane-allocation", {
        method: "PATCH",
        body: JSON.stringify({ asset_type: "crypto", ...next }),
      });
      setAlloc(updated as LaneAllocation);
      setMsg("저장되었습니다");
      setTimeout(() => setMsg(null), 2000);
    } catch (e: any) {
      setMsg(`저장 실패: ${e.message || "오류"}`);
      setTimeout(() => setMsg(null), 3000);
    } finally {
      setSaving(false);
    }
  };

  const onAgentChange = (v: number) => {
    if (!alloc) return;
    const agentPct = Math.max(0, Math.min(1, v / 100));
    const remaining = 1 - agentPct;
    const templatePct = Math.min(alloc.template_pct, remaining);
    setAlloc({ ...alloc, agent_pct: agentPct, template_pct: templatePct });
  };

  const onTemplateChange = (v: number) => {
    if (!alloc) return;
    const templatePct = Math.max(0, Math.min(1, v / 100));
    const remaining = 1 - templatePct;
    const agentPct = Math.min(alloc.agent_pct, remaining);
    setAlloc({ ...alloc, template_pct: templatePct, agent_pct: agentPct });
  };

  const agentPct = Math.round((alloc?.agent_pct ?? 0) * 100);
  const templatePct = Math.round((alloc?.template_pct ?? 0) * 100);
  const cashPct = Math.max(0, 100 - agentPct - templatePct);

  return (
    <section className="rounded-2xl border border-[#2e2e2e] bg-[#111111] p-6 space-y-5">
      <div>
        <h3 className="text-base font-semibold tracking-tight text-white">레인 자본 배분</h3>
        <p className="mt-0.5 text-xs text-[#a1a1a1]">
          에이전트 레인(검증된 엔진)과 템플릿 레인(내가 고른 전략)의 자본 비중을 조정하세요
        </p>
      </div>

      {loading ? (
        <div className="skeleton h-32 rounded-xl" />
      ) : alloc ? (
        <>
          <div className="space-y-4">
            <div>
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs font-medium text-[#a1a1a1]">에이전트 레인</span>
                <span className="font-mono text-sm font-semibold text-white tabular-nums">{agentPct}%</span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                value={agentPct}
                onChange={(e) => onAgentChange(Number(e.target.value))}
                onMouseUp={() => alloc && save({ agent_pct: alloc.agent_pct, template_pct: alloc.template_pct })}
                onTouchEnd={() => alloc && save({ agent_pct: alloc.agent_pct, template_pct: alloc.template_pct })}
                className="w-full accent-emerald-500"
              />
            </div>
            <div>
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs font-medium text-[#a1a1a1]">템플릿 레인</span>
                <span className="font-mono text-sm font-semibold text-white tabular-nums">{templatePct}%</span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                value={templatePct}
                onChange={(e) => onTemplateChange(Number(e.target.value))}
                onMouseUp={() => alloc && save({ agent_pct: alloc.agent_pct, template_pct: alloc.template_pct })}
                onTouchEnd={() => alloc && save({ agent_pct: alloc.agent_pct, template_pct: alloc.template_pct })}
                className="w-full accent-blue-500"
              />
            </div>
            <div className="rounded-lg border border-[#2e2e2e] bg-[#0f0f12] p-3 text-xs text-[#a1a1a1]">
              <div className="flex items-center justify-between">
                <span>현금 (미투자)</span>
                <span className="font-mono text-white">{cashPct}%</span>
              </div>
            </div>
          </div>
          <div className="flex items-center justify-between text-[10px] text-[#6e6e6e]">
            <span>
              {saving ? "저장 중..." : msg ?? "슬라이더를 놓으면 자동 저장됩니다"}
            </span>
          </div>
        </>
      ) : (
        <p className="text-sm text-[#a1a1a1]">자본 배분 설정을 불러올 수 없습니다</p>
      )}
    </section>
  );
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
          <div className={`flex h-10 w-10 items-center justify-center rounded-lg bg-white/10 text-sm font-bold ${iconColor}`}>
            {provider === "claude" ? "C" : "G"}
          </div>
          <div>
            <p className="text-sm font-medium text-zinc-200">{name}</p>
            <p className="text-[11px] text-[#a1a1a1]">{description}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {status === "loading" ? (
            <span className="text-xs text-[#a1a1a1]">확인 중...</span>
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
  const [sandbox, setSandbox] = useState(true);
  const [credSaving, setCredSaving] = useState(false);
  const [credError, setCredError] = useState<string | null>(null);
  const [credSuccess, setCredSuccess] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [verifyingExchange, setVerifyingExchange] = useState<string | null>(null);
  const [verifyResults, setVerifyResults] = useState<Record<string, { ok: boolean; reason?: string | null; at: number }>>({});
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

  // Auto-dismiss success/error banners after 4s / 6s
  useEffect(() => {
    if (!credSuccess) return;
    const t = setTimeout(() => setCredSuccess(null), 4000);
    return () => clearTimeout(t);
  }, [credSuccess]);
  useEffect(() => {
    if (!credError) return;
    const t = setTimeout(() => setCredError(null), 6000);
    return () => clearTimeout(t);
  }, [credError]);

  const existingCred = credentials.find((c) => c.exchange === exchange);
  const isOverwrite = Boolean(existingCred);

  const [automationEnabled, setAutomationEnabled] = useState<boolean | null>(null);
  const [automationSaving, setAutomationSaving] = useState(false);
  const [automationError, setAutomationError] = useState<string | null>(null);

  // Fetch the authoritative flag from auth-service on mount.
  // (The risk-service's automation_enabled is a separate hardcoded default
  // and isn't the source of truth for the per-user toggle.)
  const fetchAutomation = useCallback(async () => {
    try {
      const me = await gatewayFetch("/auth/me");
      setAutomationEnabled(Boolean((me as { automation_enabled?: boolean }).automation_enabled));
    } catch {
      setAutomationEnabled(null);
    }
  }, []);

  useEffect(() => {
    fetchAutomation();
  }, [fetchAutomation]);

  async function toggleAutomation(next: boolean) {
    setAutomationSaving(true);
    setAutomationError(null);
    try {
      const updated = await gatewayFetch("/settings/automation", {
        method: "PATCH",
        body: JSON.stringify({ enabled: next }),
      });
      setAutomationEnabled(
        Boolean((updated as { automation_enabled?: boolean }).automation_enabled)
      );
    } catch (e) {
      setAutomationError(e instanceof Error ? e.message : "토글 실패");
    } finally {
      setAutomationSaving(false);
    }
  }

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
        body: JSON.stringify({ exchange, api_key: apiKey.trim(), api_secret: apiSecret.trim(), sandbox }),
      });
      setCredSuccess(isOverwrite ? "거래소 키 업데이트 완료" : "거래소 연결 완료");
      setApiKey("");
      setApiSecret("");
      fetchCredentials();
      // Auto-verify after save — reassures the user the key actually works
      setTimeout(() => verifyCredential(exchange), 300);
    } catch (e) {
      setCredError(e instanceof Error ? e.message : "저장 실패");
    } finally {
      setCredSaving(false);
    }
  }

  async function verifyCredential(ex: string) {
    setVerifyingExchange(ex);
    try {
      const result = await gatewayFetch(`/settings/credentials/${ex}/verify`, { method: "POST" });
      const ok = Boolean((result as { ok?: boolean }).ok);
      const reason = (result as { reason?: string | null }).reason ?? null;
      setVerifyResults((prev) => ({ ...prev, [ex]: { ok, reason, at: Date.now() } }));
    } catch (e) {
      setVerifyResults((prev) => ({
        ...prev,
        [ex]: { ok: false, reason: e instanceof Error ? e.message : "verify_failed", at: Date.now() },
      }));
    } finally {
      setVerifyingExchange(null);
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
          <h2 className="text-2xl font-semibold tracking-tight text-white">설정</h2>
          <p className="mt-1 text-xs text-[#a1a1a1]">거래소 연결 및 AI 설정</p>
        </div>

        {/* Quick start guide */}
        <FadeInView>
          <motion.div
            className="cursor-pointer rounded-2xl border border-[#2e2e2e] bg-[#111111] p-5"
            onClick={() => setShowGuide(!showGuide)}
            whileHover={{ borderColor: "rgba(255,255,255,0.12)" }}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div>
                  <p className="text-sm font-medium text-zinc-200">시작 가이드</p>
                  <p className="text-xs text-[#a1a1a1]">3단계로 시작</p>
                </div>
              </div>
              <motion.span
                className="text-[#a1a1a1]"
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
                  <div className="mt-4 space-y-4 border-t border-[#2e2e2e] pt-4">
                    <div className="grid gap-3 md:grid-cols-3">
                      {[
                        { step: "1", title: "거래소 연결", desc: "API 키 등록" },
                        { step: "2", title: "AI 연동", desc: "연결 버튼 클릭" },
                        { step: "3", title: "자동 매매 시작", desc: "대시보드에서 확인" },
                      ].map((item) => (
                        <div key={item.step} className="rounded-xl border border-[#2e2e2e] bg-[#0f0f12] p-4">
                          <div className="mb-2 flex h-6 w-6 items-center justify-center rounded-full bg-white text-xs font-bold text-black">
                            {item.step}
                          </div>
                          <p className="text-sm font-medium text-zinc-200">{item.title}</p>
                          <p className="mt-1 text-xs text-[#a1a1a1]">{item.desc}</p>
                        </div>
                      ))}
                    </div>

                    <div className="rounded-xl border border-[#2e2e2e] bg-[#0f0f12] p-4">
                      <p className="mb-2 text-xs font-medium text-[#a1a1a1]">거래소별 연결 방법</p>
                      {EXCHANGES.map((ex) => (
                        <div key={ex.id} className="flex items-start gap-2 py-1.5">
                          <span className="mt-0.5 text-xs font-semibold text-white">{ex.name}</span>
                          <span className="text-xs text-[#a1a1a1]">{ex.guide}</span>
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
            <section className="rounded-2xl border border-[#2e2e2e] bg-[#111111] p-6 space-y-5">
              <div>
                <h3 className="text-base font-semibold tracking-tight text-white">거래소 연결하기</h3>
                <p className="mt-0.5 text-xs text-[#a1a1a1]">API 키 등록</p>
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
                <div data-tour="settings-exchange">
                  <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-[#a1a1a1]">거래소</label>
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
                    <p className="mt-1.5 text-[11px] leading-relaxed text-[#a1a1a1]">
                      {currentExchange.guide}
                    </p>
                  )}
                  {isOverwrite && (
                    <div className="mt-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-300">
                      <span className="font-medium">이미 연결된 거래소입니다.</span> 저장 시 기존 키가 덮어써집니다.
                      {existingCred?.api_key_masked && (
                        <span className="ml-1 font-mono text-[10px] text-amber-200/70">
                          (현재: {existingCred.api_key_masked})
                        </span>
                      )}
                    </div>
                  )}
                </div>
                <div data-tour="settings-api-key">
                  <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-[#a1a1a1]">API 키</label>
                  <input
                    className="input-field"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="발급받은 API 키 입력"
                    type="password"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-[#a1a1a1]">API 시크릿</label>
                  <input
                    className="input-field"
                    value={apiSecret}
                    onChange={(e) => setApiSecret(e.target.value)}
                    placeholder="발급받은 시크릿 키 입력"
                    type="password"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-[#a1a1a1]">
                    모드
                  </label>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => setSandbox(true)}
                      className={`flex-1 rounded-lg border px-3 py-2 text-xs transition-colors ${
                        sandbox
                          ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                          : "border-[#2e2e2e] bg-[#0f0f12] text-[#a1a1a1] hover:border-white/10"
                      }`}
                    >
                      테스트 (Sandbox)
                    </button>
                    <button
                      type="button"
                      onClick={() => setSandbox(false)}
                      className={`flex-1 rounded-lg border px-3 py-2 text-xs transition-colors ${
                        !sandbox
                          ? "border-red-500/40 bg-red-500/10 text-red-300"
                          : "border-[#2e2e2e] bg-[#0f0f12] text-[#a1a1a1] hover:border-white/10"
                      }`}
                    >
                      실거래 (Live)
                    </button>
                  </div>
                  {!sandbox && (
                    <p className="mt-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-[11px] leading-relaxed text-red-300">
                      ⚠ 실거래 모드: 실제 자산으로 주문이 체결됩니다. IP 제한과 출금 권한 비활성화를 꼭 확인하세요.
                    </p>
                  )}
                </div>
                <motion.button
                  data-tour="settings-save"
                  onClick={saveCredential}
                  disabled={credSaving || !apiKey || !apiSecret}
                  className="btn-primary w-full disabled:opacity-30"
                  whileTap={{ scale: 0.98 }}
                >
                  {credSaving
                    ? isOverwrite
                      ? "업데이트 중..."
                      : "연결 중..."
                    : isOverwrite
                      ? "재등록 (덮어쓰기)"
                      : "거래소 연결"}
                </motion.button>
              </div>

              {/* Saved credentials */}
              <div className="border-t border-[#2e2e2e] pt-4">
                <p className="mb-3 text-[11px] font-medium uppercase tracking-wider text-[#a1a1a1]">연결된 거래소</p>
                {credLoading ? (
                  <div className="space-y-2">
                    {[0, 1].map((i) => (
                      <div key={i} className="skeleton h-14 rounded-xl" />
                    ))}
                  </div>
                ) : credentials.length === 0 ? (
                  <div className="text-center py-4">
                    <p className="text-sm text-[#a1a1a1]">거래소 연결 필요</p>
                  </div>
                ) : (
                  <StaggerContainer className="space-y-2">
                    {credentials.map((cred) => {
                      const verify = verifyResults[cred.exchange];
                      return (
                        <StaggerItem key={cred.credential_id}>
                          <div className="flex items-center justify-between rounded-xl border border-[#2e2e2e] bg-[#0f0f12] p-3">
                            <div>
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium text-zinc-200">
                                  {EXCHANGES.find((e) => e.id === cred.exchange)?.name || cred.exchange}
                                </span>
                                {cred.sandbox ? (
                                  <span className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-300">
                                    테스트
                                  </span>
                                ) : (
                                  <span className="rounded-md border border-red-500/30 bg-red-500/10 px-1.5 py-0.5 text-[10px] text-red-300">
                                    실거래
                                  </span>
                                )}
                                {verify && (
                                  <span
                                    className={`rounded-md px-1.5 py-0.5 text-[10px] ${
                                      verify.ok
                                        ? "border border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                                        : "border border-red-500/30 bg-red-500/10 text-red-300"
                                    }`}
                                  >
                                    {verify.ok ? "✓ 연결 확인됨" : `✕ ${verify.reason || "실패"}`}
                                  </span>
                                )}
                              </div>
                              <p className="mt-0.5 font-mono text-[11px] text-[#a1a1a1]">{cred.api_key_masked}</p>
                            </div>
                            <div className="flex items-center gap-2">
                              <button
                                onClick={() => verifyCredential(cred.exchange)}
                                disabled={verifyingExchange === cred.exchange}
                                className="rounded-lg border border-white/10 px-3 py-1 text-xs text-[#a1a1a1] transition-colors hover:border-emerald-500/30 hover:text-emerald-400 disabled:opacity-30"
                              >
                                {verifyingExchange === cred.exchange ? "확인 중..." : "연결 테스트"}
                              </button>
                              <button
                                onClick={() => deleteCredential(cred.credential_id, cred.exchange)}
                                disabled={deletingId === cred.credential_id}
                                className="rounded-lg border border-white/10 px-3 py-1 text-xs text-[#a1a1a1] transition-colors hover:border-red-500/30 hover:text-red-400 disabled:opacity-30"
                              >
                                {deletingId === cred.credential_id ? "..." : "해제"}
                              </button>
                            </div>
                          </div>
                        </StaggerItem>
                      );
                    })}
                  </StaggerContainer>
                )}
              </div>
            </section>
          </FadeInView>

          <div className="space-y-6">
            {/* Lane Allocation */}
            <FadeInView delay={0.08}>
              <LaneAllocationSection />
            </FadeInView>

            {/* Risk Settings */}
            <FadeInView delay={0.1}>
              <section className="rounded-2xl border border-[#2e2e2e] bg-[#111111] p-6 space-y-4">
                <h3 className="text-base font-semibold tracking-tight text-white">리스크 관리</h3>
                {riskLoading ? (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {[0, 1, 2, 3].map((i) => (
                      <div key={i} className="skeleton h-20 rounded-xl" />
                    ))}
                  </div>
                ) : risk ? (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div className="rounded-xl border border-[#2e2e2e] bg-[#0f0f12] p-4">
                      <p className="text-[11px] font-medium uppercase tracking-wider text-[#a1a1a1]">최대 주문금액</p>
                      <p className="mt-2 font-mono text-xl font-semibold tabular-nums text-white">
                        {risk.max_notional != null ? (
                          <><span className="text-sm text-[#a1a1a1]">$</span><AnimatedNumber value={risk.max_notional} decimals={0} /></>
                        ) : "--"}
                      </p>
                    </div>
                    <div className="rounded-xl border border-[#2e2e2e] bg-[#0f0f12] p-4">
                      <p className="text-[11px] font-medium uppercase tracking-wider text-[#a1a1a1]">투자 한도</p>
                      <p className="mt-2 font-mono text-xl font-semibold tabular-nums text-white">
                        {risk.exposure_limit != null ? (
                          <><span className="text-sm text-[#a1a1a1]">$</span><AnimatedNumber value={risk.exposure_limit} decimals={0} /></>
                        ) : "--"}
                      </p>
                    </div>
                    <div className="rounded-xl border border-[#2e2e2e] bg-[#0f0f12] p-4">
                      <p className="text-[11px] font-medium uppercase tracking-wider text-[#a1a1a1]">최대 하락 허용</p>
                      <p className="mt-2 font-mono text-xl font-semibold tabular-nums text-white">
                        {risk.max_drawdown != null ? (
                          <><AnimatedNumber value={risk.max_drawdown * 100} decimals={1} /><span className="text-sm text-[#a1a1a1]">%</span></>
                        ) : "--"}
                      </p>
                    </div>
                    <div className="rounded-xl border border-[#2e2e2e] bg-[#0f0f12] p-4">
                      <div className="flex items-baseline justify-between">
                        <p className="text-[11px] font-medium uppercase tracking-wider text-[#a1a1a1]">자동 매매</p>
                        <button
                          type="button"
                          onClick={() => toggleAutomation(!automationEnabled)}
                          disabled={automationSaving || automationEnabled === null}
                          className={`px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.14em] transition-colors disabled:opacity-50 ${
                            automationEnabled
                              ? "border border-mint text-mint hover:bg-mint/10"
                              : "border border-coral text-coral hover:bg-coral/10"
                          }`}
                        >
                          {automationSaving ? "..." : automationEnabled ? "끄기" : "켜기"}
                        </button>
                      </div>
                      <p className="mt-2 text-xl font-semibold">
                        <span className={automationEnabled ? "text-emerald-400" : "text-red-400"}>
                          {automationEnabled === null ? "—" : automationEnabled ? "활성" : "비활성"}
                        </span>
                      </p>
                      {automationError && (
                        <p className="mt-1 font-mono text-[10px] text-coral">{automationError}</p>
                      )}
                      {automationEnabled === false && (
                        <p className="mt-1 font-mono text-[10px] text-[#a1a1a1]">
                          끄면 다음 신호부터 주문 안 나감
                        </p>
                      )}
                    </div>
                  </div>
                ) : (
                  <p className="text-sm text-[#a1a1a1]">리스크 설정을 불러올 수 없습니다</p>
                )}
              </section>
            </FadeInView>

            {/* Execution Config */}
            <FadeInView delay={0.12}>
              <section className="rounded-2xl border border-[#2e2e2e] bg-[#111111] p-6 space-y-4">
                <h3 className="text-base font-semibold tracking-tight text-white">실행 설정</h3>
                {execLoading ? (
                  <div className="space-y-2">
                    {[0, 1, 2].map((i) => (
                      <div key={i} className="skeleton h-14 rounded-xl" />
                    ))}
                  </div>
                ) : execConfig ? (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between rounded-xl border border-[#2e2e2e] bg-[#0f0f12] p-4">
                      <div>
                        <p className="text-[11px] font-medium uppercase tracking-wider text-[#a1a1a1]">실시간 트레이딩</p>
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
                    <div className="flex items-center justify-between rounded-xl border border-[#2e2e2e] bg-[#0f0f12] p-4">
                      <div>
                        <p className="text-[11px] font-medium uppercase tracking-wider text-[#a1a1a1]">섀도우 모드</p>
                        <p className={`mt-1 text-sm font-semibold ${execConfig.default_shadow_mode ? "text-white" : "text-[#a1a1a1]"}`}>
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
                    <div className="rounded-xl border border-[#2e2e2e] bg-[#0f0f12] p-4">
                      <p className="text-[11px] font-medium uppercase tracking-wider text-[#a1a1a1]">허용 거래소</p>
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
                                  ? "bg-white/10 text-[#a1a1a1]"
                                  : "bg-[#111111] text-[#a1a1a1]"
                              } ${isAdmin ? "cursor-pointer hover:bg-white/20" : ""}`}
                            >
                              {ex.name}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                    {isAdmin && (
                      <p className="text-[10px] text-[#a1a1a1]">
                        관리자 권한으로 위 설정을 직접 변경할 수 있습니다
                      </p>
                    )}
                  </div>
                ) : (
                  <p className="text-sm text-[#a1a1a1]">실행 설정을 불러올 수 없습니다</p>
                )}
              </section>
            </FadeInView>

            {/* 구독 플랜 */}
            <FadeInView delay={0.12}>
              <section className="rounded-2xl border border-[#2e2e2e] bg-[#111111] p-6 space-y-4">
                <div>
                  <h3 className="text-base font-semibold tracking-tight text-white">구독 플랜</h3>
                  <p className="mt-0.5 text-xs text-[#a1a1a1]">현재 플랜과 사용량을 확인하세요</p>
                </div>

                {/* Current tier display */}
                <div className="rounded-xl border border-[#2e2e2e] bg-[#16161a] p-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-[10px] font-medium uppercase tracking-wider text-[#a1a1a1]">현재 플랜</p>
                      <p className="mt-1 text-lg font-bold text-white">{profile?.plan || "FREE"}</p>
                    </div>
                    <span className="rounded-full bg-[#1a1a1a] px-3 py-1 text-xs text-[#a1a1a1]">
                      {profile?.plan === "PREMIUM" ? "모든 기능" : profile?.plan === "PRO" ? "자동매매 1자산" : "시그널 조회"}
                    </span>
                  </div>
                </div>

                {/* Tier comparison */}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {[
                    { name: "Free", price: "0원", features: ["시그널 조회 (지연)", "대시보드", "AI 채팅 5회/일"] },
                    { name: "Pro", price: "29,000원/월", features: ["실시간 시그널", "자동매매 (1자산)", "AI 채팅 50회/일"] },
                    { name: "Premium", price: "89,000원/월", features: ["전체 자산", "무제한 자동매매", "커스텀 전략", "우선 실행"] },
                  ].map(tier => (
                    <div key={tier.name} className={`rounded-xl border p-4 ${profile?.plan === tier.name.toUpperCase() ? "border-white/[0.20] bg-[#1c1c21]" : "border-[#2e2e2e] bg-[#0f0f12]"}`}>
                      <p className="text-sm font-medium text-zinc-200">{tier.name}</p>
                      <p className="mt-1 text-lg font-bold text-white">{tier.price}</p>
                      <ul className="mt-3 space-y-1">
                        {tier.features.map(f => (
                          <li key={f} className="text-[11px] text-[#a1a1a1]">• {f}</li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              </section>
            </FadeInView>

            {/* 고급: 자체 LLM 연동 (선택사항) */}
            <FadeInView delay={0.14}>
              <section className="rounded-2xl border border-[#2e2e2e] bg-[#111111] p-6 space-y-4">
                <div>
                  <h3 className="text-base font-semibold tracking-tight text-white">AI 연동 (선택사항)</h3>
                  <p className="mt-0.5 text-xs text-[#a1a1a1]">선택사항 - 기본 AI 제공</p>
                </div>
                <AiProviderCard
                  provider="claude"
                  name="Claude (Anthropic)"
                  description="Claude Sonnet/Opus로 시장 분석, 매매 판단, 도구 호출"
                  color="border-[#2e2e2e] bg-[#111111]"
                  iconColor="text-white"
                />
                <AiProviderCard
                  provider="codex"
                  name="Codex (OpenAI)"
                  description="GPT-4o로 시장 분석, 매매 판단, 도구 호출"
                  color="border-[#2e2e2e] bg-[#111111]"
                  iconColor="text-white"
                />
                <p className="text-[10px] text-[#a1a1a1]">
                  OAuth PKCE로 안전하게 연동됩니다. API 키가 서버에 저장되지 않습니다.
                </p>
              </section>
            </FadeInView>

            {/* Profile */}
            <FadeInView delay={0.15}>
              <section className="rounded-2xl border border-[#2e2e2e] bg-[#111111] p-6 space-y-4">
                <h3 className="text-base font-semibold tracking-tight text-white">내 계정</h3>
                {profile ? (
                  <div className="space-y-3">
                    <div className="rounded-xl border border-[#2e2e2e] bg-[#0f0f12] p-4">
                      <p className="text-[11px] font-medium uppercase tracking-wider text-[#a1a1a1]">이메일</p>
                      <p className="mt-1 text-sm text-white">{profile.email}</p>
                    </div>
                    {profile.roles && profile.roles.length > 0 && (
                      <div className="rounded-xl border border-[#2e2e2e] bg-[#0f0f12] p-4">
                        <p className="text-[11px] font-medium uppercase tracking-wider text-[#a1a1a1]">권한</p>
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {profile.roles.map((role) => (
                            <span
                              key={role}
                              className="rounded-full bg-white/10 px-2.5 py-0.5 text-xs font-medium text-[#a1a1a1]"
                            >
                              {role === "admin" ? "관리자" : role === "user" ? "사용자" : role}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <p className="text-sm text-[#a1a1a1]">계정 정보를 불러올 수 없습니다</p>
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
