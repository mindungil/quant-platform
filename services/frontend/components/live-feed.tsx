"use client";

import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { connectGatewaySocket } from "../lib/socket";

type FeedEvent = {
  type?: string;
  data?: Record<string, unknown>;
  raw: string;
};

const EVENT_LABELS: Record<string, string> = {
  "agent.crypto.action": "에이전트 판단",
  "order.created": "주문 생성",
  "order.filled": "주문 체결",
  "order.cancelled": "주문 취소",
  "risk.incident": "리스크 경고",
  "signal.evaluated": "시그널 평가",
  "decision.completed": "분석 완료",
};

function friendlyEvent(evt: FeedEvent): { label: string; detail: string } {
  const label = EVENT_LABELS[evt.type || ""] || "시스템 이벤트";
  const d = evt.data || {};
  const asset = (d.asset as string) || "";
  const action = (d.action as string) || "";
  const friendly = asset ? `${asset.replace("USDT", "")} ${action}`.trim() : "";
  return { label, detail: friendly };
}

function relativeTime(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "방금";
  if (sec < 3600) return `${Math.floor(sec / 60)}분 전`;
  return `${Math.floor(sec / 3600)}시간 전`;
}

export function LiveFeed() {
  const [events, setEvents] = useState<FeedEvent[]>([]);

  useEffect(() => {
    return connectGatewaySocket((payload: unknown) => {
      const p = payload as Record<string, unknown> | null;
      const evt: FeedEvent = {
        type: (p?.type as string) || undefined,
        data: (p?.data as Record<string, unknown>) || undefined,
        raw: JSON.stringify(payload),
      };
      setEvents((current) => [evt, ...current].slice(0, 6));
    });
  }, []);

  if (events.length === 0) {
    return (
      <div className="rounded-xl border border-neutral-800 bg-neutral-950 p-4">
        <h3 className="mb-2 text-sm font-medium text-[#a1a1a1]">실시간 활동</h3>
        <p className="text-xs text-[#a1a1a1]">아직 이벤트가 없습니다</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-950 p-4">
      <h3 className="mb-3 text-sm font-medium text-[#a1a1a1]">실시간 활동</h3>
      <div className="space-y-1.5">
        <AnimatePresence initial={false}>
          {events.map((evt, i) => {
            const { label, detail } = friendlyEvent(evt);
            return (
              <motion.div
                key={`${evt.raw.slice(0, 30)}-${i}`}
                initial={{ opacity: 0, x: -12 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ type: "spring", stiffness: 300, damping: 25 }}
                className="flex items-center gap-2 rounded-lg bg-neutral-900/50 px-3 py-2"
              >
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                <span className="text-xs font-medium text-[#a1a1a1]">{label}</span>
                {detail && (
                  <span className="text-xs text-[#a1a1a1]">{detail}</span>
                )}
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </div>
  );
}
