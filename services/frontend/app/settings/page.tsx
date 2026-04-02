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

const EXCHANGES = ["binance", "upbit", "alpaca"];

function SettingsContent() {
  // Credentials state
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [credLoading, setCredLoading] = useState(true);
  const [exchange, setExchange] = useState("binance");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [credSaving, setCredSaving] = useState(false);
  const [credError, setCredError] = useState<string | null>(null);
  const [credSuccess, setCredSuccess] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // Risk settings state
  const [risk, setRisk] = useState<RiskSettings | null>(null);
  const [riskLoading, setRiskLoading] = useState(true);

  // Profile state
  const [profile, setProfile] = useState<UserProfile | null>(null);

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

    // Read profile from token
    const claims = readTokenClaims();
    if (claims) {
      setProfile({
        email: claims.email ?? claims.sub ?? "unknown",
        roles: claims.roles,
      });
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
        body: JSON.stringify({
          exchange,
          api_key: apiKey.trim(),
          api_secret: apiSecret.trim(),
          sandbox: true,
        }),
      });
      setCredSuccess("인증 정보가 저장되었습니다");
      setApiKey("");
      setApiSecret("");
      fetchCredentials();
    } catch (e) {
      setCredError(e instanceof Error ? e.message : "인증 정보 저장 실패");
    } finally {
      setCredSaving(false);
    }
  }

  async function deleteCredential(credentialId: string, exchange: string) {
    setDeletingId(credentialId);
    try {
      await gatewayFetch(`/settings/credentials/${exchange}`, { method: "DELETE" });
      fetchCredentials();
    } catch (e) {
      alert(e instanceof Error ? e.message : "인증 정보 삭제 실패");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <PageTransition>
      <main className="grid gap-6">
        <h2 className="text-2xl font-semibold text-neutral-900">설정</h2>

        <div className="grid gap-6 lg:grid-cols-2">
          {/* API Credentials */}
          <FadeInView delay={0}>
            <section className="card space-y-4">
              <h3 className="text-lg font-semibold text-neutral-900">API 인증 정보</h3>

              <AnimatePresence mode="wait">
                {credError && (
                  <motion.p
                    key="cred-error"
                    initial={{ opacity: 0, y: -8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    className="rounded-lg bg-red-50 p-3 text-sm text-red-600"
                  >
                    {credError}
                  </motion.p>
                )}
                {credSuccess && (
                  <motion.p
                    key="cred-success"
                    initial={{ opacity: 0, y: -8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    className="rounded-lg bg-green-50 p-3 text-sm text-green-700"
                  >
                    {credSuccess}
                  </motion.p>
                )}
              </AnimatePresence>

              <div className="space-y-3">
                <div>
                  <label className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-neutral-500">거래소</label>
                  <select
                    className="input-field"
                    value={exchange}
                    onChange={(e) => setExchange(e.target.value)}
                  >
                    {EXCHANGES.map((ex) => (
                      <option key={ex} value={ex}>
                        {ex.charAt(0).toUpperCase() + ex.slice(1)}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-neutral-500">API 키</label>
                  <input
                    className="input-field"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="API 키 입력"
                    type="password"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-neutral-500">API 시크릿</label>
                  <input
                    className="input-field"
                    value={apiSecret}
                    onChange={(e) => setApiSecret(e.target.value)}
                    placeholder="API 시크릿 입력"
                    type="password"
                  />
                </div>
                <button
                  onClick={saveCredential}
                  disabled={credSaving}
                  className="btn-primary disabled:opacity-40"
                >
                  {credSaving ? "저장 중..." : "인증 정보 추가"}
                </button>
              </div>

              {/* Saved credentials list */}
              <div className="border-t border-neutral-200 pt-4">
                <p className="mb-2 text-xs font-medium uppercase tracking-wider text-neutral-400">저장된 인증 정보</p>
                {credLoading ? (
                  <div className="space-y-2">
                    {[0, 1].map((i) => (
                      <div key={i} className="animate-pulse rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                        <div className="h-4 w-20 rounded bg-neutral-200" />
                        <div className="mt-1 h-3 w-32 rounded bg-neutral-100" />
                      </div>
                    ))}
                  </div>
                ) : credentials.length === 0 ? (
                  <p className="text-sm text-neutral-400">저장된 인증 정보 없음</p>
                ) : (
                  <StaggerContainer className="space-y-2">
                    {credentials.map((cred) => (
                      <StaggerItem key={cred.credential_id}>
                        <div className="flex items-center justify-between rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                          <div>
                            <span className="text-sm font-medium uppercase text-neutral-900">{cred.exchange}</span>
                            <p className="mt-0.5 font-mono text-xs text-neutral-400">{cred.api_key_masked}</p>
                            {cred.sandbox && (
                              <span className="text-xs text-yellow-600">샌드박스</span>
                            )}
                          </div>
                          <button
                            onClick={() => deleteCredential(cred.credential_id, cred.exchange)}
                            disabled={deletingId === cred.credential_id}
                            className="rounded-lg border border-red-200 px-3 py-1 text-xs font-medium text-red-600 hover:bg-red-50 disabled:opacity-40"
                          >
                            {deletingId === cred.credential_id ? "..." : "삭제"}
                          </button>
                        </div>
                      </StaggerItem>
                    ))}
                  </StaggerContainer>
                )}
              </div>
            </section>
          </FadeInView>

          {/* Right column: Risk + Profile */}
          <div className="space-y-6">
            {/* Risk Settings */}
            <FadeInView delay={0.1}>
              <section className="card space-y-4">
                <h3 className="text-lg font-semibold text-neutral-900">리스크 설정</h3>
                {riskLoading ? (
                  <div className="grid grid-cols-2 gap-3">
                    {[0, 1, 2, 3].map((i) => (
                      <div key={i} className="animate-pulse rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                        <div className="h-3 w-16 rounded bg-neutral-200" />
                        <div className="mt-2 h-5 w-20 rounded bg-neutral-200" />
                      </div>
                    ))}
                  </div>
                ) : risk ? (
                  <div className="space-y-3">
                    <div className="grid grid-cols-2 gap-3">
                      <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                        <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">최대 주문금액</p>
                        <p className="mt-1 text-lg font-semibold text-neutral-900">
                          {risk.max_notional != null ? `$${risk.max_notional.toLocaleString()}` : "--"}
                        </p>
                      </div>
                      <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                        <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">노출 한도</p>
                        <p className="mt-1 text-lg font-semibold text-neutral-900">
                          {risk.exposure_limit != null ? `$${risk.exposure_limit.toLocaleString()}` : "--"}
                        </p>
                      </div>
                      <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                        <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">최대 낙폭</p>
                        <p className="mt-1 text-lg font-semibold text-neutral-900">
                          {risk.max_drawdown != null ? `${(risk.max_drawdown * 100).toFixed(1)}%` : "--"}
                        </p>
                      </div>
                      <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                        <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">자동화</p>
                        <p className={`mt-1 text-lg font-semibold ${risk.automation_enabled ? "text-green-600" : "text-red-600"}`}>
                          {risk.automation_enabled ? "활성" : "비활성"}
                        </p>
                      </div>
                    </div>

                    {/* Show any additional risk fields */}
                    {Object.entries(risk).filter(([k]) =>
                      !["max_notional", "exposure_limit", "max_drawdown", "automation_enabled"].includes(k)
                    ).length > 0 && (
                      <details className="mt-2">
                        <summary className="cursor-pointer text-xs text-neutral-400 hover:text-neutral-700">
                          전체 리스크 파라미터
                        </summary>
                        <pre className="mt-2 overflow-x-auto rounded-lg border border-neutral-100 bg-neutral-50 p-3 text-xs text-neutral-600">
                          {JSON.stringify(risk, null, 2)}
                        </pre>
                      </details>
                    )}
                  </div>
                ) : (
                  <p className="text-sm text-neutral-400">리스크 설정을 불러올 수 없습니다</p>
                )}
              </section>
            </FadeInView>

            {/* Profile */}
            <FadeInView delay={0.2}>
              <section className="card space-y-4">
                <h3 className="text-lg font-semibold text-neutral-900">프로필</h3>
                {profile ? (
                  <div className="space-y-3">
                    <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                      <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">이메일</p>
                      <p className="mt-1 text-sm text-neutral-900">{profile.email}</p>
                    </div>
                    {profile.plan && (
                      <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                        <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">플랜</p>
                        <p className="mt-1 text-sm text-neutral-900">{profile.plan}</p>
                      </div>
                    )}
                    {profile.roles && profile.roles.length > 0 && (
                      <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-3">
                        <p className="text-xs font-medium uppercase tracking-wider text-neutral-400">역할</p>
                        <div className="mt-1 flex flex-wrap gap-1">
                          {profile.roles.map((role) => (
                            <span
                              key={role}
                              className="badge bg-neutral-100 text-neutral-600"
                            >
                              {role}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <p className="text-sm text-neutral-400">프로필 정보를 불러올 수 없습니다</p>
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
