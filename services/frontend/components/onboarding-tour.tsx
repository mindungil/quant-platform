"use client";

/**
 * Terminal-native onboarding popover.
 *
 * Replaces react-joyride entirely so the tour matches the rest of the
 * Bloomberg-amber, sharp-corner, JetBrains Mono terminal aesthetic.
 * Anchors to nav items via [data-tour] attributes, draws an amber
 * outline on the target, and shows a small popover beside it.
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { usePathname } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";

type Step = {
  target: string;
  title: string;
  content: string;
};

const STORAGE_KEY = "quant_onboarding_v2";
const FIRST_PROMPT_KEY = "quant_onboarding_v2_prompt_shown";

/* ── State helpers ─────────────────────────────────────────── */

function isCompleted(): boolean {
  if (typeof window === "undefined") return true;
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return true;
  }
}
function markCompleted() {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, "1");
  } catch {}
}
function isFirstPromptShown(): boolean {
  if (typeof window === "undefined") return true;
  try {
    return localStorage.getItem(FIRST_PROMPT_KEY) === "1";
  } catch {
    return true;
  }
}
function markFirstPromptShown() {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(FIRST_PROMPT_KEY, "1");
  } catch {}
}

/* ── Steps (per route) ─────────────────────────────────────── */

const dashboardSteps: Step[] = [
  {
    target: '[data-tour="nav-dashboard"]',
    title: "메인 // SPLENDOR",
    content:
      "에이전트가 지금 무엇을 하는지 한 화면으로 보여줍니다. 데이터·모니터링은 별도 섹션에 분리되어 있어 평소엔 조용합니다.",
  },
  {
    target: '[data-tour="nav-monitoring/operations"]',
    title: "모니터링",
    content:
      "포지션·시그널 테이프·KPI 등 운영 데이터는 모니터링 섹션. Operations · Alpha Health · Soak 탭으로 나뉘어 있어요.",
  },
  {
    target: '[data-tour="nav-strategies"]',
    title: "전략",
    content:
      "8년 백테스트된 알파(momentum_ensemble · range_reversion · vol_breakout · funding_carry)를 선택·조합합니다.",
  },
  {
    target: '[data-tour="nav-agent"]',
    title: "자동매매",
    content:
      "AI 에이전트가 봉 닫힘마다 자동으로 주문을 냅니다. 리스크 데몬이 급락 시 즉시 청산합니다.",
  },
  {
    target: '[data-tour="nav-performance"]',
    title: "성과",
    content: "Sharpe · 최대낙폭(MDD) · 일일 수익률 등 정량 지표를 추적합니다.",
  },
  {
    target: '[data-tour="nav-settings"]',
    title: "설정 — 여기서 시작",
    content:
      "거래소 API 키를 먼저 등록해야 자동매매가 가능합니다. 설정 페이지에서 단계별 안내를 받습니다.",
  },
];

const settingsSteps: Step[] = [
  {
    target: '[data-tour="settings-exchange"]',
    title: "1단계 // 거래소",
    content:
      "Upbit(현물) 또는 Binance(선물)를 선택하세요. 둘 다 등록 가능.",
  },
  {
    target: '[data-tour="settings-api-key"]',
    title: "2단계 // API 키",
    content:
      "거래소에서 발급받은 Access · Secret Key를 입력. 출금 권한은 절대 체크하지 마세요.",
  },
  {
    target: '[data-tour="settings-save"]',
    title: "3단계 // 저장 · 검증",
    content:
      "저장 시 서버가 키 유효성을 검증합니다. 암호화 저장 · 언제든 삭제 가능.",
  },
];

const strategiesSteps: Step[] = [
  {
    target: '[data-tour="strategy-list"]',
    title: "전략 목록",
    content:
      "Sharpe(위험조정수익) > 1.0, MDD < 20%가 안정적. 처음에는 momentum_ensemble 추천.",
  },
];

function stepsForPath(p?: string | null): Step[] {
  if (!p) return [];
  if (p === "/dashboard" || p === "/") return dashboardSteps;
  if (p.startsWith("/settings")) return settingsSteps;
  if (p.startsWith("/strategies")) return strategiesSteps;
  return [];
}

/* ── Geometry ──────────────────────────────────────────────── */

interface Box {
  top: number;
  left: number;
  width: number;
  height: number;
}

function measure(selector: string): Box | null {
  if (typeof document === "undefined") return null;
  let el: Element | null = null;
  try {
    el = document.querySelector(selector);
  } catch {
    return null;
  }
  if (!el) return null;
  const r = (el as HTMLElement).getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return null;
  return { top: r.top, left: r.left, width: r.width, height: r.height };
}

/* ── Popover ───────────────────────────────────────────────── */

function StepPopover({
  step,
  idx,
  total,
  onNext,
  onPrev,
  onClose,
}: {
  step: Step;
  idx: number;
  total: number;
  onNext: () => void;
  onPrev: () => void;
  onClose: () => void;
}) {
  const [box, setBox] = useState<Box | null>(null);
  const [vw, setVw] = useState(0);
  const popRef = useRef<HTMLDivElement>(null);

  const update = useCallback(() => {
    setBox(measure(step.target));
    setVw(window.innerWidth);
  }, [step.target]);

  useEffect(() => {
    update();
    // re-measure on resize / scroll
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    // also poll once after a beat for late-mounting nav
    const t = setTimeout(update, 80);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
      clearTimeout(t);
    };
  }, [update]);

  // ── Layout the popover relative to box ─────────────────
  const POP_W = 320;
  const POP_H_EST = 180;
  const GUTTER = 14;
  let popLeft = 24;
  let popTop = 80;
  let placement: "below" | "above" | "center" = "below";

  if (box) {
    // Prefer below; if not enough room, go above; if too tall, center.
    const spaceBelow = window.innerHeight - (box.top + box.height);
    const spaceAbove = box.top;
    if (spaceBelow >= POP_H_EST + GUTTER) placement = "below";
    else if (spaceAbove >= POP_H_EST + GUTTER) placement = "above";
    else placement = "center";

    const targetCenterX = box.left + box.width / 2;
    popLeft = Math.max(
      14,
      Math.min(targetCenterX - POP_W / 2, vw - POP_W - 14),
    );

    if (placement === "below") popTop = box.top + box.height + GUTTER;
    else if (placement === "above") popTop = Math.max(14, box.top - POP_H_EST - GUTTER);
    else popTop = Math.max(40, window.innerHeight / 2 - POP_H_EST / 2);
  }

  // arrow X relative to popover
  const arrowX = box
    ? Math.max(18, Math.min(box.left + box.width / 2 - popLeft, POP_W - 18))
    : POP_W / 2;

  return (
    <>
      {/* dim very lightly — keep terminal mood, just enough focus */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.2 }}
        className="fixed inset-0 z-[9990] bg-black/35 backdrop-blur-[1px]"
        onClick={onClose}
      />

      {/* spotlight ring on target — amber, square, 1px */}
      {box && placement !== "center" && (
        <motion.div
          aria-hidden
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.25 }}
          className="fixed pointer-events-none z-[9991]"
          style={{
            top: box.top - 6,
            left: box.left - 6,
            width: box.width + 12,
            height: box.height + 12,
            border: "1px solid rgba(251, 189, 46, 0.85)",
            boxShadow:
              "0 0 0 9999px rgba(0,0,0,0.0), 0 0 18px rgba(251,189,46,0.55), inset 0 0 12px rgba(251,189,46,0.18)",
          }}
        >
          {/* corner brackets — match hero crosshair language */}
          <span className="absolute top-0 left-0 w-2 h-2 border-t border-l border-amber" />
          <span className="absolute top-0 right-0 w-2 h-2 border-t border-r border-amber" />
          <span className="absolute bottom-0 left-0 w-2 h-2 border-b border-l border-amber" />
          <span className="absolute bottom-0 right-0 w-2 h-2 border-b border-r border-amber" />
        </motion.div>
      )}

      {/* popover */}
      <motion.div
        ref={popRef}
        role="dialog"
        aria-label={step.title}
        initial={{ opacity: 0, y: placement === "above" ? 6 : -6 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: placement === "above" ? 6 : -6 }}
        transition={{ duration: 0.22, ease: "easeOut" }}
        className="fixed z-[9992] bg-ink-50 border border-amber/70 shadow-[0_24px_60px_rgba(0,0,0,0.55)]"
        style={{
          top: popTop,
          left: popLeft,
          width: POP_W,
        }}
      >
        {/* amber tab on top */}
        <span
          aria-hidden
          className="absolute -top-px left-3 right-3 h-[1px] bg-amber"
          style={{ boxShadow: "0 0 12px rgba(251,189,46,0.55)" }}
        />
        {/* arrow caret pointing to target */}
        {box && placement === "below" && (
          <span
            aria-hidden
            className="absolute -top-[6px] w-3 h-3 bg-ink-50 border-t border-l border-amber/70 rotate-45"
            style={{ left: arrowX - 6 }}
          />
        )}
        {box && placement === "above" && (
          <span
            aria-hidden
            className="absolute -bottom-[6px] w-3 h-3 bg-ink-50 border-b border-r border-amber/70 rotate-45"
            style={{ left: arrowX - 6 }}
          />
        )}

        <div className="px-5 pt-5 pb-4">
          <div className="flex items-baseline justify-between mb-3">
            <p className="label-eyebrow-amber">
              STEP {String(idx + 1).padStart(2, "0")} / {String(total).padStart(2, "0")}
            </p>
            <button
              onClick={onClose}
              aria-label="투어 닫기"
              className="font-mono text-[10px] tracking-[0.18em] uppercase text-paper-low hover:text-coral transition-colors"
            >
              ESC ✕
            </button>
          </div>

          <h3 className="font-mono font-semibold text-[15px] text-paper tracking-tight uppercase">
            {step.title}
          </h3>
          <p className="mt-2.5 font-prose text-[13px] leading-relaxed text-paper-dim">
            {step.content}
          </p>
        </div>

        {/* footer */}
        <div className="border-t border-rule px-5 py-3 flex items-center justify-between gap-3">
          {/* progress */}
          <div className="flex items-center gap-1">
            {Array.from({ length: total }).map((_, i) => (
              <span
                key={i}
                aria-hidden
                className={`h-[2px] w-4 ${i <= idx ? "bg-amber" : "bg-rule-strong"}`}
                style={i === idx ? { boxShadow: "0 0 8px rgba(251,189,46,0.6)" } : undefined}
              />
            ))}
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={onPrev}
              disabled={idx === 0}
              className="font-mono text-[11px] tracking-[0.16em] uppercase text-paper-mute hover:text-paper transition-colors disabled:opacity-30 disabled:cursor-not-allowed px-2 py-1"
            >
              ← 이전
            </button>
            <button
              onClick={onNext}
              className="font-mono text-[11px] font-semibold tracking-[0.16em] uppercase bg-amber text-ink px-3 py-1.5 hover:bg-[#ffce5e] transition-colors"
            >
              {idx + 1 >= total ? "완료" : "다음 →"}
            </button>
          </div>
        </div>
      </motion.div>
    </>
  );
}

/* ── First-visit pill ──────────────────────────────────────── */

function FirstVisitPill({ onStart, onDismiss }: { onStart: () => void; onDismiss: () => void }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 8 }}
      transition={{ duration: 0.4, delay: 1.6, ease: "easeOut" }}
      className="fixed z-[9988] bottom-5 right-5 flex items-stretch border border-rule-loud bg-ink-50/95 backdrop-blur-sm shadow-[0_18px_40px_rgba(0,0,0,0.45)]"
    >
      <button
        onClick={onStart}
        className="group flex items-baseline gap-2.5 px-4 py-2.5 transition-colors hover:bg-ink-100"
      >
        <span className="agent-breath" aria-hidden style={{ width: 8, height: 8 }} />
        <span className="font-mono text-[10px] tracking-[0.18em] uppercase text-paper-mute group-hover:text-paper">
          처음 사용
        </span>
        <span className="font-mono text-[12px] tracking-[0.08em] uppercase text-amber">
          투어 보기
        </span>
        <span className="font-mono text-amber transition-transform group-hover:translate-x-0.5">→</span>
      </button>
      <button
        onClick={onDismiss}
        aria-label="안내 닫기"
        className="border-l border-rule-loud px-3 font-mono text-[10px] uppercase tracking-[0.18em] text-paper-low hover:text-coral transition-colors"
      >
        ✕
      </button>
    </motion.div>
  );
}

/* ── Main exported component ───────────────────────────────── */

export function OnboardingTour() {
  const pathname = usePathname();
  const [run, setRun] = useState(false);
  const [stepIdx, setStepIdx] = useState(0);
  const [steps, setSteps] = useState<Step[]>([]);
  const [showPill, setShowPill] = useState(false);

  // pick steps for this route
  useEffect(() => {
    setSteps(stepsForPath(pathname));
    setRun(false);
    setStepIdx(0);
  }, [pathname]);

  // first-visit pill — only on dashboard, only if never shown + not completed
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onboardingHome = pathname === "/dashboard" || pathname === "/";
    if (!onboardingHome) {
      setShowPill(false);
      return;
    }
    if (isCompleted() || isFirstPromptShown()) {
      setShowPill(false);
      return;
    }
    setShowPill(true);
  }, [pathname]);

  // manual start trigger
  useEffect(() => {
    const handler = () => {
      setStepIdx(0);
      setRun(true);
      setShowPill(false);
    };
    window.addEventListener("quant:start-tour", handler);
    return () => window.removeEventListener("quant:start-tour", handler);
  }, []);

  // ESC closes
  useEffect(() => {
    if (!run) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") finish();
      else if (e.key === "ArrowRight") next();
      else if (e.key === "ArrowLeft") prev();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, stepIdx, steps]);

  const next = () => {
    if (stepIdx + 1 >= steps.length) {
      finish();
    } else {
      setStepIdx(stepIdx + 1);
    }
  };
  const prev = () => setStepIdx(Math.max(0, stepIdx - 1));
  const finish = () => {
    setRun(false);
    markCompleted();
  };

  const startFromPill = () => {
    setShowPill(false);
    markFirstPromptShown();
    setStepIdx(0);
    setRun(true);
  };
  const dismissPill = () => {
    setShowPill(false);
    markFirstPromptShown();
  };

  return (
    <>
      <AnimatePresence>
        {showPill && !run && (
          <FirstVisitPill onStart={startFromPill} onDismiss={dismissPill} />
        )}
      </AnimatePresence>
      <AnimatePresence>
        {run && steps.length > 0 && stepIdx < steps.length && (
          <StepPopover
            key={stepIdx}
            step={steps[stepIdx]}
            idx={stepIdx}
            total={steps.length}
            onNext={next}
            onPrev={prev}
            onClose={finish}
          />
        )}
      </AnimatePresence>
    </>
  );
}

export function startTour() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent("quant:start-tour"));
}

export function resetOnboarding() {
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(FIRST_PROMPT_KEY);
  } catch {}
}
