"use client";

import { useEffect, useState } from "react";
import { gatewayFetch } from "../../lib/api";

export default function SettingsPage() {
  const [exchange, setExchange] = useState("binance");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [result, setResult] = useState("");
  const [settings, setSettings] = useState<Record<string, any> | null>(null);

  useEffect(() => {
    gatewayFetch("/settings").then(setSettings).catch(() => setSettings(null));
  }, []);

  async function saveCredentials() {
    const response = await gatewayFetch("/gateway/settings/credentials", {
      method: "POST",
      body: JSON.stringify({ user_id: "ignored", exchange, api_key: apiKey, api_secret: apiSecret, sandbox: true })
    });
    setResult(JSON.stringify(response, null, 2));
  }

  async function checkRisk() {
    const response = await gatewayFetch("/gateway/settings/risk", {
      method: "POST",
      body: JSON.stringify({
        asset: "BTCUSDT",
        requested_notional: 1000,
        max_notional: 5000,
        current_drawdown: 0.01,
        current_exposure: 500,
        exposure_limit: 50000,
        automation_enabled: true
      })
    });
    setResult(JSON.stringify(response, null, 2));
  }

  return (
    <main className="grid gap-6 lg:grid-cols-2">
      <section className="panel space-y-3">
        <h2 className="text-2xl font-semibold">Credentials</h2>
        <input className="w-full rounded-2xl bg-white/10 px-4 py-3" value={exchange} onChange={(e) => setExchange(e.target.value)} />
        <input className="w-full rounded-2xl bg-white/10 px-4 py-3" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="API Key" />
        <input className="w-full rounded-2xl bg-white/10 px-4 py-3" value={apiSecret} onChange={(e) => setApiSecret(e.target.value)} placeholder="API Secret" />
        <button className="rounded-full bg-sand px-4 py-2 text-ink" onClick={saveCredentials}>Save Credentials</button>
      </section>
      <section className="panel space-y-3">
        <h2 className="text-2xl font-semibold">Risk and Execution</h2>
        <button className="rounded-full border border-white/20 px-4 py-2" onClick={checkRisk}>Check Risk Defaults</button>
        <pre className="overflow-x-auto rounded-2xl bg-black/20 p-4 text-xs">{JSON.stringify(settings, null, 2)}</pre>
        <pre className="overflow-x-auto rounded-2xl bg-black/20 p-4 text-xs">{result}</pre>
      </section>
    </main>
  );
}
