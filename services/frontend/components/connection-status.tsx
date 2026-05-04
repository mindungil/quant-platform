"use client";

import { useEffect, useState } from "react";
import { gatewayFetch } from "../lib/api";
import Link from "next/link";

interface ConnectionState {
  exchanges: { id: string; name: string; connected: boolean }[];
  system: "online" | "offline" | "loading";
}

export function ConnectionStatus() {
  const [state, setState] = useState<ConnectionState>({
    exchanges: [],
    system: "loading",
  });

  useEffect(() => {
    async function check() {
      try {
        const [creds] = await Promise.all([
          gatewayFetch("/settings/credentials").catch(() => []),
        ]);
        const credList = Array.isArray(creds)
          ? creds
          : (creds as any)?.credentials ?? [];
        const exchanges = [
          { id: "binance", name: "BINANCE", connected: credList.some((c: any) => c.exchange === "binance") },
          { id: "upbit",   name: "UPBIT",   connected: credList.some((c: any) => c.exchange === "upbit") },
        ];
        setState({ exchanges, system: "online" });
      } catch {
        setState((prev) => ({ ...prev, system: "offline" }));
      }
    }
    check();
  }, []);

  if (state.system === "loading") return null;
  const anyConnected = state.exchanges.some((e) => e.connected);
  const live = state.system === "online";

  return (
    <Link
      href="/settings"
      className="group flex items-center gap-3 px-3 py-1.5 border border-rule-loud transition-colors hover:border-amber"
    >
      <span className={live ? "amber-led" : "amber-led-static led-coral"} aria-hidden />
      <span className="font-mono text-[10px] tracking-[0.18em] uppercase text-paper-mute group-hover:text-paper">
        {anyConnected
          ? state.exchanges.filter((e) => e.connected).map((e) => e.name).join(" · ")
          : "NO EXCHANGE"}
      </span>
      {!anyConnected && (
        <span className="font-mono text-[10px] tracking-[0.16em] uppercase text-amber">
          LINK →
        </span>
      )}
    </Link>
  );
}
