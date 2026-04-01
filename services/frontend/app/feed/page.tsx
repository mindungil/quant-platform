"use client";

import { useEffect, useState } from "react";
import { gatewayFetch } from "../../lib/api";

export default function FeedPage() {
  const [feed, setFeed] = useState<any[]>([]);

  useEffect(() => {
    gatewayFetch("/feed")
      .then((response) => setFeed(response.items ?? []))
      .catch(() => setFeed([]));
  }, []);

  return (
    <main className="panel">
      <h2 className="mb-4 text-2xl font-semibold">Agent Feed</h2>
      <div className="space-y-4">
        {feed.map((item, index) => (
          <article key={`${item.record.id}-${index}`} className="rounded-2xl bg-black/20 p-4">
            <div className="flex items-center justify-between">
              <span className="text-sm uppercase tracking-[0.2em] text-mint">{item.record.asset}</span>
              <span>{item.record.action}</span>
            </div>
            <p className="mt-2 text-xs uppercase tracking-[0.18em] text-white/50">{item.record.strategy_name ?? "strategy"} / score {item.record.signal_score ?? "n/a"}</p>
            <p className="mt-3 text-white/80">{item.record.reasoning}</p>
          </article>
        ))}
      </div>
    </main>
  );
}
