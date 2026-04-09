"use client";
import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
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

const CATEGORIES = ["전체", "보수", "공격", "시장중립"];

export default function TemplatesPage() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [category, setCategory] = useState("전체");
  const [activating, setActivating] = useState<string | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    gatewayFetch("/strategies/templates")
      .then((data) => setTemplates(Array.isArray(data) ? data : []))
      .catch(() => {});
  }, []);

  const filtered =
    category === "전체"
      ? templates
      : templates.filter((t) => t.category === category);

  const activate = async (id: string) => {
    setActivating(id);
    setMessage("");
    try {
      await gatewayFetch(`/strategies/templates/${id}/activate`, {
        method: "POST",
      });
      setMessage("전략이 활성화되었습니다");
      setTimeout(() => setMessage(""), 3000);
    } catch (e: any) {
      setMessage(`활성화 실패: ${e.message || "알 수 없는 오류"}`);
    } finally {
      setActivating(null);
    }
  };

  const riskColor = (level: string) => {
    if (level === "low") return "text-emerald-400";
    if (level === "high") return "text-red-400";
    return "text-zinc-300";
  };

  const riskLabel = (level: string) => {
    if (level === "low") return "낮음";
    if (level === "high") return "높음";
    return "중간";
  };

  return (
    <main className="min-h-screen bg-zinc-950 px-6 py-16">
      <div className="mx-auto max-w-6xl">
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <h1 className="text-4xl font-bold text-white text-glow">
            전략 템플릿
          </h1>
          <p className="mt-2 text-zinc-400">
            한 번의 클릭으로 검증된 전략을 시작하세요
          </p>
        </motion.div>

        {/* Category filter */}
        <div className="mt-8 flex gap-2">
          {CATEGORIES.map((c) => (
            <button
              key={c}
              onClick={() => setCategory(c)}
              className={`rounded-lg px-4 py-2 text-sm font-medium transition-all ${
                category === c
                  ? "bg-white text-black"
                  : "border border-white/[0.06] text-zinc-400 hover:border-white/[0.10]"
              }`}
            >
              {c}
            </button>
          ))}
        </div>

        {/* Templates grid */}
        <div className="mt-8 grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          <AnimatePresence>
            {filtered.map((t, i) => (
              <motion.div
                key={t.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ delay: i * 0.05 }}
                className="rounded-2xl border border-white/[0.06] bg-white/[0.03] p-6 hover-lift"
              >
                <div className="flex items-center justify-between">
                  <span className="rounded-full bg-white/[0.08] px-2.5 py-0.5 text-[10px] font-medium text-zinc-300">
                    {t.category}
                  </span>
                  <span
                    className={`text-[10px] font-medium ${riskColor(
                      t.risk_level
                    )}`}
                  >
                    위험도 {riskLabel(t.risk_level)}
                  </span>
                </div>

                <h3 className="mt-3 text-lg font-bold text-white">{t.name}</h3>
                <p className="mt-2 text-xs text-zinc-400 leading-relaxed">
                  {t.description}
                </p>

                <p className="mt-3 border-l-2 border-white/[0.20] pl-3 text-xs italic text-zinc-500">
                  &ldquo;{t.testimonial}&rdquo;
                </p>

                <div className="mt-4 flex items-center justify-between">
                  <span className="text-[10px] text-zinc-500">
                    예상 월 수익률
                  </span>
                  <span className="text-sm font-semibold text-emerald-400">
                    {t.expected_monthly_return}
                  </span>
                </div>

                <button
                  onClick={() => activate(t.id)}
                  disabled={activating === t.id}
                  className="mt-4 w-full rounded-lg bg-white py-2 text-sm font-semibold text-black transition-all hover:shadow-[0_0_20px_rgba(255,255,255,0.15)] disabled:opacity-40"
                >
                  {activating === t.id ? "활성화 중..." : "1-Click 활성화"}
                </button>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>

        {message && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="fixed bottom-8 left-1/2 -translate-x-1/2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-6 py-3 text-sm text-emerald-400"
          >
            {message}
          </motion.div>
        )}
      </div>
    </main>
  );
}
