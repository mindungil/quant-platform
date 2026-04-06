"use client";

import { useEffect, useState, useCallback } from "react";

import { AdminGuard } from "../../../components/admin-guard";
import { gatewayFetch } from "../../../lib/api";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  AnimatePresence,
  motion,
} from "../../../components/motion";

type PreflightCheck = {
  name: string;
  passed: boolean;
  message?: string;
};

type PreflightResult = {
  all_passed: boolean;
  checks: PreflightCheck[];
};

type ExecutionConfig = {
  live_trading_enabled: boolean;
  allowed_exchanges: string[];
  default_shadow_mode: boolean;
};

export default function AdminTradingPage() {
  const [execConfig, setExecConfig] = useState<ExecutionConfig | null>(null);
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [enableLoading, setEnableLoading] = useState(false);
  const [stopLoading, setStopLoading] = useState(false);
  const [stopConfirm, setStopConfirm] = useState(false);
  const [enableConfirm, setEnableConfirm] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Editable config state
  const [editExchanges, setEditExchanges] = useState("");
  const [editShadow, setEditShadow] = useState(false);
  const [configDirty, setConfigDirty] = useState(false);
  const [configSaving, setConfigSaving] = useState(false);

  const loadConfig = useCallback(async () => {
    try {
      const config = await gatewayFetch("/admin/execution/config");
      setExecConfig(config);
      setEditExchanges(config.allowed_exchanges?.join(", ") ?? "");
      setEditShadow(config.default_shadow_mode);
      setConfigDirty(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "설정을 불러오지 못했습니다");
    }
  }, []);

  useEffect(() => {
    loadConfig();
  }, [loadConfig]);

  // Auto-clear success message
  useEffect(() => {
    if (!success) return;
    const t = setTimeout(() => setSuccess(""), 4000);
    return () => clearTimeout(t);
  }, [success]);

  async function runPreflight() {
    setPreflightLoading(true);
    setError("");
    setSuccess("");
    try {
      const result = await gatewayFetch("/admin/execution/pre-flight", { method: "POST", body: JSON.stringify({}) });
      setPreflight(result);
      if (result.all_passed) {
        setSuccess("모든 사전 점검을 통과했습니다.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "사전 점검 실패");
      setPreflight(null);
    } finally {
      setPreflightLoading(false);
    }
  }

  async function enableLive() {
    setEnableLoading(true);
    setError("");
    setSuccess("");
    try {
      await gatewayFetch("/admin/execution/enable-live", { method: "POST", body: JSON.stringify({}) });
      setSuccess("실시간 트레이딩이 활성화되었습니다.");
      setEnableConfirm(false);
      await loadConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : "실시간 트레이딩 활성화 실패");
    } finally {
      setEnableLoading(false);
    }
  }

  async function emergencyStop() {
    setStopLoading(true);
    setError("");
    setSuccess("");
    try {
      await gatewayFetch("/admin/execution/emergency-stop", { method: "POST", body: JSON.stringify({}) });
      setSuccess("긴급 정지가 실행되었습니다. 모든 트레이딩이 중단되었습니다.");
      setStopConfirm(false);
      setPreflight(null);
      await loadConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : "긴급 정지 실패");
    } finally {
      setStopLoading(false);
    }
  }

  async function saveConfig() {
    setConfigSaving(true);
    setError("");
    setSuccess("");
    try {
      const exchanges = editExchanges
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      await gatewayFetch("/admin/execution/config", {
        method: "PATCH",
        body: JSON.stringify({
          allowed_exchanges: exchanges,
          default_shadow_mode: editShadow,
        }),
      });
      setSuccess("실행 설정이 업데이트되었습니다.");
      await loadConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : "설정 저장 실패");
    } finally {
      setConfigSaving(false);
    }
  }

  const currentMode = execConfig
    ? execConfig.live_trading_enabled
      ? "live"
      : execConfig.default_shadow_mode
        ? "shadow"
        : "stopped"
    : "unknown";

  const statusLabels: Record<string, string> = {
    live: "라이브",
    shadow: "섀도우",
    stopped: "정지됨",
    unknown: "알 수 없음",
  };

  const statusTextColors: Record<string, string> = {
    live: "text-emerald-400",
    shadow: "text-neutral-600",
    stopped: "text-red-400",
    unknown: "text-neutral-400",
  };

  return (
    <AdminGuard>
      <PageTransition>
        <main className="grid gap-6">
          {/* Header with status */}
          <section className="rounded border border-white/[0.06] bg-white/[0.03] p-6">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">TRADING CONTROL</p>
                <h2 className="mt-1 text-2xl font-semibold text-white">실시간 트레이딩 제어</h2>
                <p className="mt-1 text-sm text-neutral-500">
                  사전 점검, 실행 제어, 설정 관리
                </p>
              </div>
              <div className="rounded border border-white/[0.06] px-6 py-3 text-center">
                <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">현재 상태</p>
                <p className={`mt-1 font-mono text-2xl font-semibold ${statusTextColors[currentMode]}`}>
                  {statusLabels[currentMode]}
                </p>
              </div>
            </div>
          </section>

          {/* Alerts */}
          <AnimatePresence>
            {error && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.25 }}
                className="rounded border border-white/[0.06] px-4 py-3 text-sm text-red-400"
              >
                {error}
              </motion.div>
            )}
          </AnimatePresence>
          <AnimatePresence>
            {success && (
              <motion.div
                initial={{ opacity: 0, y: -8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.3 }}
                className="rounded border border-white/[0.06] px-4 py-3 text-sm text-emerald-400"
              >
                {success}
              </motion.div>
            )}
          </AnimatePresence>

          {/* Pre-flight Checks */}
          <section className="rounded border border-white/[0.06] bg-white/[0.03] p-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">PRE-FLIGHT</p>
                <h3 className="mt-1 text-lg font-semibold text-white">사전 점검</h3>
              </div>
              <button
                className="btn-primary disabled:opacity-50"
                disabled={preflightLoading}
                onClick={runPreflight}
              >
                {preflightLoading ? "실행 중..." : "사전 점검 실행"}
              </button>
            </div>
            <AnimatePresence>
              {preflight && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  className="mt-4 space-y-2"
                >
                  <StaggerContainer className="space-y-2">
                    {preflight.checks.map((check) => (
                      <StaggerItem key={check.name}>
                        <div className="flex items-center gap-3 rounded border border-white/[0.06] bg-white/[0.03] px-4 py-3">
                          <span className={`inline-flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full text-white text-xs ${
                            check.passed ? "bg-emerald-500" : "bg-red-500"
                          }`}>
                            {check.passed ? "\u2713" : "\u2717"}
                          </span>
                          <div className="min-w-0 flex-1">
                            <p className="text-sm font-medium text-white">{check.name}</p>
                            {check.message && (
                              <p className="text-xs text-neutral-400">{check.message}</p>
                            )}
                          </div>
                          <span className={`font-mono text-xs font-semibold ${check.passed ? "text-white" : "text-red-400"}`}>
                            {check.passed ? "통과" : "실패"}
                          </span>
                        </div>
                      </StaggerItem>
                    ))}
                  </StaggerContainer>
                  <div className={`mt-2 rounded border px-4 py-2 text-center text-sm font-semibold ${
                    preflight.all_passed ? "border-white/[0.06] text-emerald-400" : "border-white/[0.06] text-red-400"
                  }`}>
                    {preflight.all_passed ? "모든 점검 통과" : "일부 점검 실패"}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
            {!preflight && !preflightLoading && (
              <p className="mt-4 text-sm text-neutral-400">
                사전 점검을 실행하여 인증 정보, 거래소 연결, 리스크 한도, 활성 전략을 확인하세요.
              </p>
            )}
          </section>

          {/* Enable Live / Emergency Stop */}
          <div className="grid gap-4 sm:grid-cols-2">
            {/* Enable Live */}
            <section className="rounded border border-white/[0.06] bg-white/[0.03] p-6">
              <h3 className="mb-3 text-lg font-semibold text-white">실시간 트레이딩 활성화</h3>
              <p className="mb-4 text-sm text-neutral-500">
                활성화 전 모든 사전 점검을 통과해야 합니다.
              </p>
              <AnimatePresence mode="wait">
                {!enableConfirm ? (
                  <motion.div key="enable-btn" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}>
                    <button
                      className="w-full rounded border border-cyan-500 bg-cyan-500 py-3 text-sm font-semibold text-white hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-40"
                      disabled={!preflight?.all_passed || execConfig?.live_trading_enabled === true}
                      onClick={() => setEnableConfirm(true)}
                    >
                      {execConfig?.live_trading_enabled ? "이미 활성화됨" : "실시간 트레이딩 활성화"}
                    </button>
                  </motion.div>
                ) : (
                  <motion.div
                    key="enable-confirm"
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    transition={{ duration: 0.2 }}
                    className="space-y-3"
                  >
                    <p className="text-sm font-medium text-white">
                      확인: 실제 자금으로 실시간 트레이딩을 활성화하시겠습니까?
                    </p>
                    <div className="flex gap-3">
                      <button
                        className="flex-1 rounded bg-cyan-500 py-3 text-sm font-semibold text-white hover:bg-cyan-400 disabled:opacity-50"
                        disabled={enableLoading}
                        onClick={enableLive}
                      >
                        {enableLoading ? "활성화 중..." : "예, 활성화"}
                      </button>
                      <button
                        className="btn-secondary flex-1 py-3"
                        onClick={() => setEnableConfirm(false)}
                      >
                        취소
                      </button>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </section>

            {/* Emergency Stop */}
            <section className="rounded border border-white/[0.06] bg-white/[0.03] p-6">
              <h3 className="mb-3 text-lg font-semibold text-white">긴급 정지</h3>
              <p className="mb-4 text-sm text-neutral-500">
                모든 트레이딩 작업을 즉시 중단하고 미체결 주문을 취소합니다.
              </p>
              <AnimatePresence mode="wait">
                {!stopConfirm ? (
                  <motion.div key="stop-btn" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}>
                    <button
                      className="w-full rounded bg-red-600 py-3 text-sm font-bold text-white hover:bg-red-700"
                      onClick={() => setStopConfirm(true)}
                    >
                      긴급 정지
                    </button>
                  </motion.div>
                ) : (
                  <motion.div
                    key="stop-confirm"
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    transition={{ duration: 0.2 }}
                    className="space-y-3"
                  >
                    <p className="text-sm font-medium text-red-400">
                      정말 정지하시겠습니까? 모든 트레이딩이 즉시 중단됩니다.
                    </p>
                    <div className="flex gap-3">
                      <button
                        className="flex-1 rounded bg-red-600 py-3 text-sm font-bold text-white hover:bg-red-700 disabled:opacity-50"
                        disabled={stopLoading}
                        onClick={emergencyStop}
                      >
                        {stopLoading ? "정지 중..." : "예, 전부 정지"}
                      </button>
                      <button
                        className="btn-secondary flex-1 py-3"
                        onClick={() => setStopConfirm(false)}
                      >
                        취소
                      </button>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </section>
          </div>

          {/* Execution Config Editor */}
          <section className="rounded border border-white/[0.06] bg-white/[0.03] p-6">
            <p className="text-sm font-medium uppercase tracking-wider text-neutral-400">EXECUTION CONFIG</p>
            <h3 className="mt-2 text-lg font-semibold text-white">실행 설정</h3>
            {execConfig ? (
              <div className="mt-4 space-y-4">
                <div className="rounded border border-white/[0.06] bg-white/[0.03] p-4">
                  <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">live_trading_enabled</p>
                  <p className={`mt-1 font-mono text-lg font-semibold ${execConfig.live_trading_enabled ? "text-emerald-400" : "text-red-400"}`}>
                    {execConfig.live_trading_enabled ? "true" : "false"}
                  </p>
                  <p className="mt-1 text-xs text-neutral-400">
                    위의 실시간 활성화 / 긴급 정지 버튼으로 제어됩니다.
                  </p>
                </div>

                <div className="rounded border border-white/[0.06] bg-white/[0.03] p-4">
                  <label className="block text-xs font-medium uppercase tracking-wider text-neutral-400">allowed_exchanges</label>
                  <input
                    type="text"
                    value={editExchanges}
                    onChange={(e) => {
                      setEditExchanges(e.target.value);
                      setConfigDirty(true);
                    }}
                    placeholder="binance, bybit, okx"
                    className="mt-2 w-full rounded border border-white/[0.06] bg-white/[0.03] px-3 py-2 text-sm text-white outline-none focus:border-cyan-500"
                  />
                  <p className="mt-1 text-xs text-neutral-400">쉼표로 구분된 거래소 식별자</p>
                </div>

                <div className="rounded border border-white/[0.06] bg-white/[0.03] p-4">
                  <label className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      checked={editShadow}
                      onChange={(e) => {
                        setEditShadow(e.target.checked);
                        setConfigDirty(true);
                      }}
                      className="h-4 w-4 rounded border-white/[0.10] accent-cyan-500"
                    />
                    <span className="text-sm text-white">default_shadow_mode</span>
                  </label>
                  <p className="mt-2 text-xs text-neutral-400">
                    활성화 시 새 전략이 기본적으로 섀도우 모드로 시작됩니다.
                  </p>
                </div>

                <button
                  className="btn-primary disabled:opacity-40"
                  disabled={!configDirty || configSaving}
                  onClick={saveConfig}
                >
                  {configSaving ? "저장 중..." : "설정 저장"}
                </button>
              </div>
            ) : (
              <p className="mt-4 text-sm text-neutral-400">실행 설정 로딩 중...</p>
            )}
          </section>
        </main>
      </PageTransition>
    </AdminGuard>
  );
}
