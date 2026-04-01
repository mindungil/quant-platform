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
    <main className="card">
      <h2 className="mb-6 text-2xl font-semibold text-neutral-900">Agent Feed</h2>
      <div className="space-y-4">
        {feed.length === 0 && (
          <p className="text-sm text-neutral-400">No feed items yet.</p>
        )}
        {feed.map((item, index) => (
          <article key={`${item.record.id}-${index}`} className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium uppercase tracking-wide text-neutral-500">{item.record.asset}</span>
              <span className={`badge ${
                item.record.action === "BUY" ? "bg-green-50 text-green-700" :
                item.record.action === "SELL" ? "bg-red-50 text-red-700" :
                "bg-neutral-100 text-neutral-500"
              }`}>
                {item.record.action}
              </span>
            </div>
            <p className="mt-2 text-xs text-neutral-400">
              {item.record.strategy_name ?? "strategy"} / score {item.record.signal_score ?? "n/a"}
            </p>
            <p className="mt-3 text-sm text-neutral-700">{item.record.reasoning}</p>
          </article>
        ))}
      </div>
    </main>
  );
}
