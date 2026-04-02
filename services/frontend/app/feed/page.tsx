"use client";

import { useEffect, useState } from "react";
import { gatewayFetch } from "../../lib/api";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
  AnimatePresence,
  motion,
} from "../../components/motion";

function formatRelativeTime(timestamp: string | undefined): string {
  if (!timestamp) return "";
  try {
    const now = Date.now();
    const then = new Date(timestamp).getTime();
    const diffMs = now - then;
    if (diffMs < 0) return "방금";
    const seconds = Math.floor(diffMs / 1000);
    if (seconds < 60) return `${seconds}초 전`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}분 전`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}시간 전`;
    const days = Math.floor(hours / 24);
    return `${days}일 전`;
  } catch {
    return "";
  }
}

function actionBorderColor(action: string | undefined): string {
  if (!action) return "border-l-neutral-300";
  const a = action.toUpperCase();
  if (a === "BUY") return "border-l-green-400";
  if (a === "SELL") return "border-l-red-400";
  if (a === "HOLD") return "border-l-yellow-400";
  return "border-l-neutral-300";
}

/* eslint-disable @typescript-eslint/no-explicit-any */
export default function FeedPage() {
  const [feed, setFeed] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    gatewayFetch("/feed")
      .then((response: any) => setFeed(response.items ?? []))
      .catch(() => setFeed([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <PageTransition>
      <main className="card">
        <h2 className="mb-6 text-2xl font-semibold text-neutral-900">활동 피드</h2>
        <div className="space-y-4">
          {loading ? (
            <StaggerContainer className="space-y-4">
              {[0, 1, 2, 3].map((i) => (
                <StaggerItem key={i}>
                  <div className="animate-pulse rounded-lg border border-neutral-200 bg-neutral-50 p-4">
                    <div className="flex items-center justify-between">
                      <div className="h-4 w-20 rounded bg-neutral-200" />
                      <div className="h-5 w-12 rounded-full bg-neutral-200" />
                    </div>
                    <div className="mt-2 h-3 w-40 rounded bg-neutral-100" />
                    <div className="mt-3 h-4 w-full rounded bg-neutral-100" />
                    <div className="mt-1 h-4 w-3/4 rounded bg-neutral-100" />
                  </div>
                </StaggerItem>
              ))}
            </StaggerContainer>
          ) : feed.length === 0 ? (
            <p className="text-sm text-neutral-400">이벤트 없음</p>
          ) : (
            <AnimatePresence initial={false}>
              {feed.map((item: any, index: number) => (
                <motion.article
                  key={`${item.record.id}-${index}`}
                  initial={{ opacity: 0, y: -16 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -16 }}
                  transition={{ duration: 0.3, ease: "easeOut" }}
                  className={`rounded-lg border border-neutral-200 border-l-4 ${actionBorderColor(item.record.action)} bg-neutral-50 p-4`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium uppercase tracking-wide text-neutral-500">{item.record.asset}</span>
                    <div className="flex items-center gap-2">
                      {item.record.timestamp && (
                        <span className="text-xs text-neutral-400">
                          {formatRelativeTime(item.record.timestamp)}
                        </span>
                      )}
                      <span className={`badge ${
                        item.record.action === "BUY" ? "bg-green-50 text-green-700" :
                        item.record.action === "SELL" ? "bg-red-50 text-red-700" :
                        "bg-neutral-100 text-neutral-500"
                      }`}>
                        {item.record.action}
                      </span>
                    </div>
                  </div>
                  <p className="mt-2 text-xs text-neutral-400">
                    {item.record.strategy_name ?? "strategy"} / score {item.record.signal_score ?? "n/a"}
                  </p>
                  <p className="mt-3 text-sm text-neutral-700">{item.record.reasoning}</p>
                </motion.article>
              ))}
            </AnimatePresence>
          )}
        </div>
      </main>
    </PageTransition>
  );
}
