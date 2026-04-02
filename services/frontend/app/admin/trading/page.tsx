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

  const statusColors: Record<string, string> = {
    live: "border-green-400 bg-green-50",
    shadow: "border-yellow-400 bg-yellow-50",
    stopped: "border-red-400 bg-red-50",
    unknown: "border-neutral-200 bg-neutral-50",
  };

  const statusTextColors: Record<string, string> = {
    live: "text-green-700",
    shadow: "text-yellow-700",
    stopped: "text-red-700",
    unknown: "text-neutral-400",
  };

  const statusLabels: Record<string, string> = {
    live: "라이브",
    shadow: "섀도우",
    stopped: "정지됨",
    unknown: "알 수 없음",
  };

  return (
    <AdminGuard>
      <PageTransition>
        <main className="grid gap-6">
          {/* Header with status */}
          <section className="card">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-3xl font-semibold text-neutral-900">실시간 트레이딩 제어</h2>
                <p className="mt-2 text-neutral-500">
                  사전 점검, 실행 제어, 설정 관리
                </p>
              </div>
              <motion.div
                className={`rounded-xl border px-6 py-3 text-center ${statusColors[currentMode]}`}
                animate={{
                  borderColor: currentMode === "live" ? "#4ade80" : currentMode === "shadow" ? "#facc15" : currentMode === "stopped" ? "#f87171" : "#e5e5e5",
                }}
                transition={{ duration: 0.5 }}
              >
                <p className="text-xs text-neutral-400">현재 상태</p>
                <motion.p
                  key={currentMode}
                  initial={{ opacity: 0, scale: 0.8 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ duration: 0.3 }}
                  className={`text-2xl font-bold ${statusTextColors[currentMode]}`}
                >
                  {statusLabels[currentMode]}
                </motion.p>
              </motion.div>
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
                className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700"
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
                className="rounded-lg bg-green-50 px-4 py-3 text-sm text-green-700"
              >
                {success}
              </motion.div>
            )}
          </AnimatePresence>

          {/* Pre-flight Checks */}
          <section className="card">
            <div className="flex items-center justify-between">
              <h3 className="text-xl font-semibold text-neutral-900">사전 점검</h3>
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
                        <div className="flex items-center gap-3 rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3">
                          <motion.span
                            initial={{ scale: 0 }}
                            animate={{ scale: 1 }}
                            transition={{ type: "spring", stiffness: 400, damping: 15, delay: 0.1 }}
                            className={`inline-flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full text-white text-xs ${
                              check.passed ? "bg-green-500" : "bg-red-500"
                            }`}
                          >
                            {check.passed ? "\u2713" : "\u2717"}
                          </motion.span>
                          <div className="min-w-0 flex-1">
                            <p className="text-sm font-medium text-neutral-900">{check.name}</p>
                            {check.message && (
                              <p className="text-xs text-neutral-400">{check.message}</p>
                            )}
                          </div>
                          <span
                            className={`text-xs font-semibold ${check.passed ? "text-green-600" : "text-red-600"}`}
                          >
                            {check.passed ? "통과" : "실패"}
                          </span>
                        </div>
                      </StaggerItem>
                    ))}
                  </StaggerContainer>
                  <div className={`mt-2 rounded-lg px-4 py-2 text-center text-sm font-semibold ${
                    preflight.all_passed ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"
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
            <section className="card">
              <h3 className="mb-3 text-xl font-semibold text-neutral-900">실시간 트레이딩 활성화</h3>
              <p className="mb-4 text-sm text-neutral-500">
                활성화 전 모든 사전 점검을 통과해야 합니다.
              </p>
              <AnimatePresence mode="wait">
                {!enableConfirm ? (
                  <motion.div key="enable-btn" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}>
                    <button
                      className="w-full rounded-lg bg-green-600 py-3 text-sm font-semibold text-white hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-40"
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
                    <p className="text-sm font-medium text-yellow-700">
                      확인: 실제 자금으로 실시간 트레이딩을 활성화하시겠습니까?
                    </p>
                    <div className="flex gap-3">
                      <button
                        className="flex-1 rounded-lg bg-green-600 py-3 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-50"
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
            <section className="card">
              <h3 className="mb-3 text-xl font-semibold text-neutral-900">긴급 정지</h3>
              <p className="mb-4 text-sm text-neutral-500">
                모든 트레이딩 작업을 즉시 중단하고 미체결 주문을 취소합니다.
              </p>
              <AnimatePresence mode="wait">
                {!stopConfirm ? (
                  <motion.div key="stop-btn" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}>
                    <button
                      className="w-full rounded-lg bg-red-600 py-3 text-lg font-bold text-white hover:bg-red-700"
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
                    <p className="text-sm font-medium text-red-700">
                      정말 정지하시겠습니까? 모든 트레이딩이 즉시 중단됩니다.
                    </p>
                    <div className="flex gap-3">
                      <button
                        className="flex-1 rounded-lg bg-red-600 py-3 text-sm font-bold text-white hover:bg-red-700 disabled:opacity-50"
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
          <section className="card">
            <h3 className="mb-4 text-xl font-semibold text-neutral-900">실행 설정</h3>
            {execConfig ? (
              <div className="space-y-4">
                <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
                  <p className="text-xs text-neutral-400">live_trading_enabled</p>
                  <p className={`mt-1 text-lg font-semibold ${execConfig.live_trading_enabled ? "text-green-600" : "text-red-600"}`}>
                    {execConfig.live_trading_enabled ? "true" : "false"}
                  </p>
                  <p className="mt-1 text-xs text-neutral-400">
                    위의 실시간 활성화 / 긴급 정지 버튼으로 제어됩니다.
                  </p>
                </div>

                <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
                  <label className="block text-xs text-neutral-400">allowed_exchanges</label>
                  <input
                    type="text"
                    value={editExchanges}
                    onChange={(e) => {
                      setEditExchanges(e.target.value);
                      setConfigDirty(true);
                    }}
                    placeholder="binance, bybit, okx"
                    className="mt-2 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-900 outline-none focus:border-neutral-900 focus:ring-1 focus:ring-neutral-900"
                  />
                  <p className="mt-1 text-xs text-neutral-400">쉼표로 구분된 거래소 식별자</p>
                </div>

                <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
                  <label className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      checked={editShadow}
                      onChange={(e) => {
                        setEditShadow(e.target.checked);
                        setConfigDirty(true);
                      }}
                      className="h-4 w-4 rounded border-neutral-300 accent-neutral-900"
                    />
                    <span className="text-sm text-neutral-900">default_shadow_mode</span>
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
              <p className="text-sm text-neutral-400">실행 설정 로딩 중...</p>
            )}
          </section>
        </main>
      </PageTransition>
    </AdminGuard>
  );
}
