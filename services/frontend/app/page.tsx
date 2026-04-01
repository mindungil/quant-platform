"use client";

import { useState } from "react";
import { gatewayFetch, setToken } from "../lib/api";

export default function HomePage() {
  const [email, setEmail] = useState("demo@example.com");
  const [password, setPassword] = useState("password123");
  const [displayName, setDisplayName] = useState("Demo");
  const [message, setMessage] = useState("");

  async function register() {
    const response = await gatewayFetch("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, display_name: displayName, plan: "premium" })
    });
    setMessage(JSON.stringify(response, null, 2));
  }

  async function login() {
    const response = await gatewayFetch("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password })
    });
    setToken(response.access_token);
    setMessage("Logged in. Redirecting to /dashboard.");
    window.setTimeout(() => {
      window.location.href = "/dashboard";
    }, 300);
  }

  return (
    <main className="grid gap-6 md:grid-cols-[1.1fr_0.9fr]">
      <section className="panel">
        <p className="mb-2 text-sm uppercase tracking-[0.3em] text-mint">B2C Entry</p>
        <h2 className="mb-3 text-3xl font-semibold">Quant Command Deck</h2>
        <p className="max-w-2xl text-white/80">
          This Next.js frontend replaces the previous FastAPI shell and consumes the existing gateway surface for auth,
          dashboard, signals, feed, strategies, settings, and websocket events.
        </p>
      </section>
      <section className="panel space-y-3">
        <h3 className="text-xl font-semibold">Register or Login</h3>
        <input className="w-full rounded-2xl bg-white/10 px-4 py-3" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        <input className="w-full rounded-2xl bg-white/10 px-4 py-3" value={email} onChange={(e) => setEmail(e.target.value)} />
        <input className="w-full rounded-2xl bg-white/10 px-4 py-3" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <div className="flex gap-3">
          <button className="rounded-full bg-sand px-4 py-2 text-ink" onClick={register}>Register</button>
          <button className="rounded-full border border-white/20 px-4 py-2" onClick={login}>Login</button>
        </div>
        <pre className="rounded-2xl bg-black/20 p-3 text-xs">{message}</pre>
      </section>
    </main>
  );
}
