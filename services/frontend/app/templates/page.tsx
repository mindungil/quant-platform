"use client";
import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { AuthGuard } from "../../components/auth-guard";
import { gatewayFetch } from "../../lib/api";

interface Template {
  id: string;
  name: string;
  category: string;
  description: string;
  testimonial: string;
  asset_type: string;
  risk_level: string;
  expected_monthly_return: string;
}

interface Subscription {
  id: string;
  user_id: string;
  template_id: string;
  asset_type: string;
  status: "enabled" | "paused" | "stopped";
  weight: number;
  created_at: string;
}

const CATEGORIES = ["전체", "보수", "공격", "시장중립"];

export default function TemplatesPage() {
  return (
    <AuthGuard>
      <TemplatesPageInner />
    </AuthGuard>
  );
}

function TemplatesPageInner() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [subs, setSubs] = useState<Subscription[]>([]);
  const [category, setCategory] = useState("전체");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [messageType, setMessageType] = useState<"ok" | "err">("ok");

  const loadSubs = () => {
    gatewayFetch("/templates/subscriptions")
      .then((data) => setSubs(Array.isArray(data) ? data : []))
      .catch(() => setSubs([]));
  };

  useEffect(() => {
    gatewayFetch("/strategies/templates")
      .then((data) => setTemplates(Array.isArray(data) ? data : []))
      .catch(() => {});
    loadSubs();
  }, []);

  const subByTemplate = (templateId: string): Subscription | undefined =>
    subs.find((s) => s.template_id === templateId && s.status !== "stopped");

  const filtered =
    category === "전체"
      ? templates
      : templates.filter((t) => t.category === category);

  const flash = (msg: string, type: "ok" | "err" = "ok") => {
    setMessage(msg);
    setMessageType(type);
    setTimeout(() => setMessage(""), 3000);
  };

  const subscribe = async (templateId: string) => {
    setBusyId(templateId);
    try {
      await gatewayFetch("/templates/subscriptions", {
        method: "POST",
        body: JSON.stringify({ template_id: templateId, asset_type: "crypto", weight: 1.0 }),
      });
      flash("구독을 시작했습니다", "ok");
      loadSubs();
    } catch (e: any) {
      flash(`구독 실패: ${e.message || "알 수 없는 오류"}`, "err");
    } finally {
      setBusyId(null);
    }
  };

  const updateStatus = async (sub: Subscription, status: "enabled" | "paused") => {
    setBusyId(sub.template_id);
    try {
      await gatewayFetch(`/templates/subscriptions/${sub.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      flash(status === "enabled" ? "재개했습니다" : "일시중지했습니다", "ok");
      loadSubs();
    } catch (e: any) {
      flash(`변경 실패: ${e.message || "알 수 없는 오류"}`, "err");
    } finally {
      setBusyId(null);
    }
  };

  const unsubscribe = async (sub: Subscription) => {
    setBusyId(sub.template_id);
    try {
      await gatewayFetch(`/templates/subscriptions/${sub.id}`, { method: "DELETE" });
      flash("구독을 해지했습니다", "ok");
      loadSubs();
    } catch (e: any) {
      flash(`해지 실패: ${e.message || "알 수 없는 오류"}`, "err");
    } finally {
      setBusyId(null);
    }
  };

  const riskColor = (level: string) => {
    if (level === "low") return "text-emerald-400";
    if (level === "high") return "text-red-400";
    return "text-[#a1a1a1]";
  };

  const riskLabel = (level: string) => {
    if (level === "low") return "낮음";
    if (level === "high") return "높음";
    return "중간";
  };

  return (
    <main className="min-h-screen bg-zinc-950 px-6 py-16">
      <div className="mx-auto max-w-6xl">
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
          <h1 className="text-4xl font-bold text-white text-glow">전략 템플릿</h1>
          <p className="mt-2 text-[#a1a1a1]">
            구독한 템플릿은 템플릿 레인에서 자동으로 실행됩니다.
          </p>
          <p className="mt-1 text-xs text-[#6e6e6e]">
            에이전트 레인과 별개로 작동하며, 자본 배분은 설정에서 조정할 수 있습니다.
          </p>
        </motion.div>

        <div className="mt-8 flex gap-2">
          {CATEGORIES.map((c) => (
            <button
              key={c}
              onClick={() => setCategory(c)}
              className={`rounded-lg px-4 py-2 text-sm font-medium transition-all ${
                category === c
                  ? "bg-white text-black"
                  : "border border-[#2e2e2e] text-[#a1a1a1] hover:border-[#3e3e3e]"
              }`}
            >
              {c}
            </button>
          ))}
        </div>

        <div className="mt-8 grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          <AnimatePresence>
            {filtered.map((t, i) => {
              const sub = subByTemplate(t.id);
              const state = sub?.status;
              const busy = busyId === t.id;

              return (
                <motion.div
                  key={t.id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ delay: i * 0.05 }}
                  className={`rounded-2xl border p-6 hover-lift ${
                    state === "enabled"
                      ? "border-emerald-500/40 bg-emerald-500/[0.03]"
                      : state === "paused"
                      ? "border-amber-500/30 bg-amber-500/[0.03]"
                      : "border-[#2e2e2e] bg-[#111111]"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="rounded-full bg-[#1a1a1a] px-2.5 py-0.5 text-[10px] font-medium text-[#a1a1a1]">
                      {t.category}
                    </span>
                    <div className="flex items-center gap-1.5">
                      {state === "enabled" && (
                        <span className="flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-400">
                          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />구독 중
                        </span>
                      )}
                      {state === "paused" && (
                        <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-400">
                          일시중지
                        </span>
                      )}
                      <span className={`text-[10px] font-medium ${riskColor(t.risk_level)}`}>
                        위험도 {riskLabel(t.risk_level)}
                      </span>
                    </div>
                  </div>

                  <h3 className="mt-3 text-lg font-bold text-white">{t.name}</h3>
                  <p className="mt-2 text-xs text-[#a1a1a1] leading-relaxed">{t.description}</p>

                  <p className="mt-3 border-l-2 border-white/[0.20] pl-3 text-xs italic text-[#a1a1a1]">
                    &ldquo;{t.testimonial}&rdquo;
                  </p>

                  <div className="mt-4 flex items-center justify-between">
                    <span className="text-[10px] text-[#a1a1a1]">예상 월 수익률</span>
                    <span className="text-sm font-semibold text-emerald-400">
                      {t.expected_monthly_return}
                    </span>
                  </div>

                  <div className="mt-4 space-y-2">
                    {!sub && (
                      <button
                        onClick={() => subscribe(t.id)}
                        disabled={busy}
                        className="w-full rounded-lg bg-white py-2 text-sm font-semibold text-black transition-all hover:shadow-[0_0_20px_rgba(255,255,255,0.15)] disabled:opacity-40"
                      >
                        {busy ? "구독 중..." : "구독하기"}
                      </button>
                    )}
                    {sub && state === "enabled" && (
                      <>
                        <button
                          onClick={() => updateStatus(sub, "paused")}
                          disabled={busy}
                          className="w-full rounded-lg border border-amber-500/30 bg-amber-500/10 py-2 text-sm font-medium text-amber-400 transition-all hover:bg-amber-500/15 disabled:opacity-40"
                        >
                          {busy ? "..." : "일시중지"}
                        </button>
                        <button
                          onClick={() => unsubscribe(sub)}
                          disabled={busy}
                          className="w-full rounded-lg border border-[#2e2e2e] py-2 text-xs text-[#a1a1a1] transition-all hover:border-red-500/30 hover:text-red-400 disabled:opacity-40"
                        >
                          구독 해지
                        </button>
                      </>
                    )}
                    {sub && state === "paused" && (
                      <>
                        <button
                          onClick={() => updateStatus(sub, "enabled")}
                          disabled={busy}
                          className="w-full rounded-lg bg-emerald-500 py-2 text-sm font-semibold text-black transition-all hover:bg-emerald-400 disabled:opacity-40"
                        >
                          {busy ? "..." : "재개"}
                        </button>
                        <button
                          onClick={() => unsubscribe(sub)}
                          disabled={busy}
                          className="w-full rounded-lg border border-[#2e2e2e] py-2 text-xs text-[#a1a1a1] transition-all hover:border-red-500/30 hover:text-red-400 disabled:opacity-40"
                        >
                          구독 해지
                        </button>
                      </>
                    )}
                  </div>
                </motion.div>
              );
            })}
          </AnimatePresence>
        </div>

        {message && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className={`fixed bottom-8 left-1/2 -translate-x-1/2 rounded-lg border px-6 py-3 text-sm ${
              messageType === "ok"
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                : "border-red-500/30 bg-red-500/10 text-red-400"
            }`}
          >
            {message}
          </motion.div>
        )}
      </div>
    </main>
  );
}
