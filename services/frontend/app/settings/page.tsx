"use client";

import { useEffect, useState, useCallback } from "react";
import { gatewayFetch, readTokenClaims } from "../../lib/api";
import { AuthGuard } from "../../components/auth-guard";

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
      setCredError("API Key and Secret are required");
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
      setCredSuccess("Credentials saved successfully");
      setApiKey("");
      setApiSecret("");
      fetchCredentials();
    } catch (e) {
      setCredError(e instanceof Error ? e.message : "Failed to save credentials");
    } finally {
      setCredSaving(false);
    }
  }

  async function deleteCredential(credentialId: string) {
    setDeletingId(credentialId);
    try {
      await gatewayFetch(`/settings/credentials/${credentialId}`, { method: "DELETE" });
      fetchCredentials();
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to delete credential");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <main className="grid gap-6">
      <h2 className="text-2xl font-semibold">Settings</h2>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* API Credentials */}
        <section className="panel space-y-4">
          <h3 className="text-lg font-semibold">API Credentials</h3>

          {credError && (
            <p className="rounded-xl bg-red-500/10 p-3 text-sm text-red-400">{credError}</p>
          )}
          {credSuccess && (
            <p className="rounded-xl bg-green-500/10 p-3 text-sm text-green-400">{credSuccess}</p>
          )}

          <div className="space-y-3">
            <div>
              <label className="text-xs uppercase tracking-wider text-mint">Exchange</label>
              <select
                className="mt-1 w-full rounded-2xl bg-white/10 px-4 py-3 text-sm"
                value={exchange}
                onChange={(e) => setExchange(e.target.value)}
              >
                {EXCHANGES.map((ex) => (
                  <option key={ex} value={ex} className="bg-ink">
                    {ex.charAt(0).toUpperCase() + ex.slice(1)}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-mint">API Key</label>
              <input
                className="mt-1 w-full rounded-2xl bg-white/10 px-4 py-3 text-sm"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="Enter API key"
                type="password"
              />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-mint">API Secret</label>
              <input
                className="mt-1 w-full rounded-2xl bg-white/10 px-4 py-3 text-sm"
                value={apiSecret}
                onChange={(e) => setApiSecret(e.target.value)}
                placeholder="Enter API secret"
                type="password"
              />
            </div>
            <button
              onClick={saveCredential}
              disabled={credSaving}
              className="rounded-full bg-sand px-4 py-2 text-sm font-medium text-ink hover:bg-sand/90 disabled:opacity-40"
            >
              {credSaving ? "Saving..." : "Add Credentials"}
            </button>
          </div>

          {/* Saved credentials list */}
          <div className="border-t border-white/10 pt-4">
            <p className="mb-2 text-xs uppercase tracking-wider text-mint">Saved Credentials</p>
            {credLoading ? (
              <p className="animate-pulse text-sm text-white/50">Loading...</p>
            ) : credentials.length === 0 ? (
              <p className="text-sm text-white/50">No credentials saved</p>
            ) : (
              <div className="space-y-2">
                {credentials.map((cred) => (
                  <div
                    key={cred.credential_id}
                    className="flex items-center justify-between rounded-xl bg-black/20 p-3"
                  >
                    <div>
                      <span className="text-sm font-medium uppercase">{cred.exchange}</span>
                      <p className="mt-0.5 font-mono text-xs text-white/50">{cred.api_key_masked}</p>
                      {cred.sandbox && (
                        <span className="text-xs text-sand">sandbox</span>
                      )}
                    </div>
                    <button
                      onClick={() => deleteCredential(cred.credential_id)}
                      disabled={deletingId === cred.credential_id}
                      className="rounded-full border border-red-400/30 px-3 py-1 text-xs text-red-300 hover:bg-red-500/10 disabled:opacity-40"
                    >
                      {deletingId === cred.credential_id ? "..." : "Delete"}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>

        {/* Right column: Risk + Profile */}
        <div className="space-y-6">
          {/* Risk Settings */}
          <section className="panel space-y-4">
            <h3 className="text-lg font-semibold">Risk Settings</h3>
            {riskLoading ? (
              <p className="animate-pulse text-sm text-white/50">Loading...</p>
            ) : risk ? (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div className="rounded-xl bg-black/20 p-3">
                    <p className="text-xs uppercase tracking-wider text-mint">Max Notional</p>
                    <p className="mt-1 text-lg font-semibold">
                      {risk.max_notional != null ? `$${risk.max_notional.toLocaleString()}` : "--"}
                    </p>
                  </div>
                  <div className="rounded-xl bg-black/20 p-3">
                    <p className="text-xs uppercase tracking-wider text-mint">Exposure Limit</p>
                    <p className="mt-1 text-lg font-semibold">
                      {risk.exposure_limit != null ? `$${risk.exposure_limit.toLocaleString()}` : "--"}
                    </p>
                  </div>
                  <div className="rounded-xl bg-black/20 p-3">
                    <p className="text-xs uppercase tracking-wider text-mint">Max Drawdown</p>
                    <p className="mt-1 text-lg font-semibold">
                      {risk.max_drawdown != null ? `${(risk.max_drawdown * 100).toFixed(1)}%` : "--"}
                    </p>
                  </div>
                  <div className="rounded-xl bg-black/20 p-3">
                    <p className="text-xs uppercase tracking-wider text-mint">Automation</p>
                    <p className={`mt-1 text-lg font-semibold ${risk.automation_enabled ? "text-green-400" : "text-red-400"}`}>
                      {risk.automation_enabled ? "Enabled" : "Disabled"}
                    </p>
                  </div>
                </div>

                {/* Show any additional risk fields */}
                {Object.entries(risk).filter(([k]) =>
                  !["max_notional", "exposure_limit", "max_drawdown", "automation_enabled"].includes(k)
                ).length > 0 && (
                  <details className="mt-2">
                    <summary className="cursor-pointer text-xs text-white/50 hover:text-white/80">
                      All Risk Parameters
                    </summary>
                    <pre className="mt-2 overflow-x-auto rounded-xl bg-black/20 p-3 text-xs text-white/70">
                      {JSON.stringify(risk, null, 2)}
                    </pre>
                  </details>
                )}
              </div>
            ) : (
              <p className="text-sm text-white/50">Unable to load risk settings</p>
            )}
          </section>

          {/* Profile */}
          <section className="panel space-y-4">
            <h3 className="text-lg font-semibold">Profile</h3>
            {profile ? (
              <div className="space-y-3">
                <div className="rounded-xl bg-black/20 p-3">
                  <p className="text-xs uppercase tracking-wider text-mint">Email</p>
                  <p className="mt-1 text-sm">{profile.email}</p>
                </div>
                {profile.plan && (
                  <div className="rounded-xl bg-black/20 p-3">
                    <p className="text-xs uppercase tracking-wider text-mint">Plan</p>
                    <p className="mt-1 text-sm">{profile.plan}</p>
                  </div>
                )}
                {profile.roles && profile.roles.length > 0 && (
                  <div className="rounded-xl bg-black/20 p-3">
                    <p className="text-xs uppercase tracking-wider text-mint">Roles</p>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {profile.roles.map((role) => (
                        <span
                          key={role}
                          className="rounded-full bg-mint/20 px-2 py-0.5 text-xs text-mint"
                        >
                          {role}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <p className="text-sm text-white/50">Profile information unavailable</p>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}

export default function SettingsPage() {
  return (
    <AuthGuard>
      <SettingsContent />
    </AuthGuard>
  );
}
