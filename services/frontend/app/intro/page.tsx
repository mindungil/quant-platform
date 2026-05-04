"use client";

import { useEffect, useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { getToken, readTokenClaims } from "../../lib/api";

/* ── Ticker data stream — real prices from market-data gateway ── */
const TICKER_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "LINK", "AVAX", "DOT"];

interface TickerQuote {
  symbol: string;
  price: number;
  change_pct?: number;
}

async function fetchTickers(): Promise<TickerQuote[]> {
  // Try bulk tickers endpoint first
  try {
    const res = await fetch(`/api/gateway/market-data/tickers`, { cache: "no-store" });
    if (res.ok) {
      const data = await res.json();
      const items = Array.isArray(data) ? data : (data?.tickers ?? []);
      const parsed: TickerQuote[] = [];
      for (const t of items) {
        const sym = (t.symbol || t.base || "").replace("USDT", "").replace("USD", "");
        if (!sym || !TICKER_SYMBOLS.includes(sym)) continue;
        const price = Number(t.price ?? t.last ?? t.close);
        if (!Number.isFinite(price)) continue;
        parsed.push({
          symbol: sym,
          price,
          change_pct: Number(t.change_pct ?? t.change_24h ?? t.percent_change_24h),
        });
      }
      if (parsed.length > 0) return parsed;
    }
  } catch {
    /* fall through */
  }

  // Fallback — fetch latest close from candles endpoint per symbol
  const results = await Promise.all(
    TICKER_SYMBOLS.map(async (sym) => {
      try {
        const r = await fetch(`/api/gateway/market-data/candles/${sym}USDT?interval=1h&limit=1`, { cache: "no-store" });
        if (!r.ok) return null;
        const d = await r.json();
        const arr = Array.isArray(d) ? d : (d?.candles ?? []);
        const last = arr[arr.length - 1];
        if (!last) return null;
        const price = Number(last.close ?? last.c ?? last[4]);
        if (!Number.isFinite(price)) return null;
        return { symbol: sym, price } as TickerQuote;
      } catch {
        return null;
      }
    })
  );
  return results.filter((x): x is TickerQuote => x !== null);
}

function formatTick(q: TickerQuote): string {
  const priceStr = q.price >= 1000
    ? q.price.toFixed(0)
    : q.price >= 10
      ? q.price.toFixed(2)
      : q.price.toFixed(4);
  if (Number.isFinite(q.change_pct)) {
    const dir = (q.change_pct as number) >= 0 ? "+" : "-";
    return `${q.symbol} $${priceStr} ${dir}${Math.abs(q.change_pct as number).toFixed(2)}%`;
  }
  return `${q.symbol} $${priceStr}`;
}

function useTicker() {
  const [ticks, setTicks] = useState<string[]>([]);
  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      const quotes = await fetchTickers();
      if (cancelled) return;
      // Empty on failure — no fake data
      const strings = quotes.map(formatTick);
      setTicks(strings);
    };
    refresh();
    const iv = setInterval(refresh, 15000);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, []);
  return ticks;
}

/* ── Floating grid lines ── */
function GridOverlay() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden opacity-[0.03]">
      <svg width="100%" height="100%" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <pattern id="grid" width="60" height="60" patternUnits="userSpaceOnUse">
            <path d="M 60 0 L 0 0 0 60" fill="none" stroke="white" strokeWidth="0.5" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid)" />
      </svg>
    </div>
  );
}

/* ── Data rain columns ── */
function DataRain({ ticks }: { ticks: string[] }) {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      {/* Left column */}
      <div className="absolute left-[8%] top-0 bottom-0 w-px bg-gradient-to-b from-transparent via-white/[0.04] to-transparent" />
      <div className="absolute left-[8%] top-0 flex flex-col gap-1 -translate-x-1/2 intro-rain-col opacity-[0.07]">
        {ticks.map((t, i) => (
          <span key={i} className="whitespace-nowrap font-mono text-[10px] text-white">{t}</span>
        ))}
      </div>
      {/* Right column */}
      <div className="absolute right-[8%] top-0 bottom-0 w-px bg-gradient-to-b from-transparent via-white/[0.04] to-transparent" />
      <div className="absolute right-[8%] top-0 flex flex-col gap-1 -translate-x-1/2 intro-rain-col-reverse opacity-[0.07]">
        {[...ticks].reverse().map((t, i) => (
          <span key={i} className="whitespace-nowrap font-mono text-[10px] text-white">{t}</span>
        ))}
      </div>
    </div>
  );
}

/* ── Cinematic sequence phases ── */
const PHASES = [
  { key: "logo", duration: 2200 },
  { key: "tagline", duration: 2400 },
  { key: "features", duration: 3600 },
  { key: "cta", duration: 0 },
] as const;

export default function IntroPage() {
  const router = useRouter();
  const ticks = useTicker();
  const [phase, setPhase] = useState(0);
  const [skipped, setSkipped] = useState(false);

  // Already logged in → skip
  useEffect(() => {
    const token = getToken();
    if (token) {
      const claims = readTokenClaims();
      if (claims?.exp && claims.exp * 1000 > Date.now()) {
        router.replace("/dashboard");
      }
    }
  }, [router]);

  // Auto-advance phases
  useEffect(() => {
    if (skipped || phase >= PHASES.length - 1) return;
    const timer = setTimeout(() => setPhase((p) => p + 1), PHASES[phase].duration);
    return () => clearTimeout(timer);
  }, [phase, skipped]);

  const skipToEnd = () => {
    setSkipped(true);
    setPhase(PHASES.length - 1);
  };

  const enter = () => router.push("/login");

  return (
    <div
      className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden bg-[#050508] cursor-pointer"
      onClick={phase < PHASES.length - 1 ? skipToEnd : undefined}
    >
      <GridOverlay />
      <DataRain ticks={ticks} />

      {/* Ambient glow */}
      <div className="pointer-events-none absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-72 h-72 sm:w-[600px] sm:h-[600px] rounded-full bg-white/[0.015] blur-[120px]" />

      {/* Top scanline */}
      <motion.div
        className="pointer-events-none absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-transparent via-white/20 to-transparent"
        initial={{ opacity: 0 }}
        animate={{ opacity: [0, 1, 0] }}
        transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
      />

      {/* ── Phase 0: Logo reveal ── */}
      <AnimatePresence mode="wait">
        {phase >= 0 && (
          <motion.div
            key="logo-phase"
            className="absolute inset-0 flex flex-col items-center justify-center z-10"
            initial={{ opacity: 0 }}
            animate={{ opacity: phase === 0 ? 1 : 0.3 }}
            transition={{ duration: 0.8 }}
          >
            <motion.div
              className="flex h-20 w-20 items-center justify-center rounded-2xl border border-white/10 bg-white"
              initial={{ scale: 0, rotate: -180 }}
              animate={{ scale: 1, rotate: 0 }}
              transition={{ type: "spring", stiffness: 120, damping: 14, delay: 0.3 }}
            >
              <span className="text-4xl font-black text-black tracking-tighter">Q</span>
            </motion.div>
            <motion.div
              className="mt-6 overflow-hidden"
              initial={{ width: 0 }}
              animate={{ width: "auto" }}
              transition={{ duration: 0.6, delay: 0.8, ease: [0.22, 1, 0.36, 1] }}
            >
              <h1 className="text-3xl font-bold tracking-[-0.06em] text-white whitespace-nowrap">
                QUANT
              </h1>
            </motion.div>
            <motion.p
              className="mt-2 text-sm tracking-[0.3em] uppercase text-white/30 font-light"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 1.2, duration: 0.5 }}
            >
              Autonomous Trading Engine
            </motion.p>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Phase 1: Tagline ── */}
      <AnimatePresence>
        {phase >= 1 && phase <= 2 && (
          <motion.div
            key="tagline"
            className="absolute inset-0 flex flex-col items-center justify-center z-20"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.6 }}
          >
            <motion.p
              className="text-center text-lg sm:text-xl font-light text-white/70 leading-relaxed max-w-md px-6"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.8, delay: 0.2 }}
            >
              시장은 쉬지 않습니다
            </motion.p>
            <motion.p
              className="mt-3 text-center text-2xl sm:text-3xl font-semibold text-white tracking-tight max-w-lg px-6"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.8, delay: 0.8 }}
            >
              당신의 알파도 멈추지 않아야 합니다
            </motion.p>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Phase 2: Features ── */}
      <AnimatePresence>
        {phase === 2 && (
          <motion.div
            key="features"
            className="absolute inset-0 flex flex-col items-center justify-center z-20"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.6, delay: 0.4 }}
          >
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-6 sm:gap-10 px-8 max-w-3xl">
              {[
                { num: "01", title: "검증된 알파", desc: "DSR·PBO 이중 게이트로\n과적합을 차단합니다" },
                { num: "02", title: "기관급 실행", desc: "메이커 시뮬레이션 기반\n최적 체결을 추구합니다" },
                { num: "03", title: "24/7 자율 운용", desc: "실시간 드리프트 감지와\n자동 킬스위치를 갖춥니다" },
              ].map((f, i) => (
                <motion.div
                  key={f.num}
                  className="text-center sm:text-left"
                  initial={{ opacity: 0, y: 30 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.6, delay: 0.6 + i * 0.25 }}
                >
                  <span className="font-mono text-xs text-white/20 tracking-widest">{f.num}</span>
                  <h3 className="mt-2 text-lg font-semibold text-white tracking-tight">{f.title}</h3>
                  <p className="mt-2 text-sm text-white/40 leading-relaxed whitespace-pre-line">{f.desc}</p>
                </motion.div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Phase 3: CTA ── */}
      <AnimatePresence>
        {phase >= 3 && (
          <motion.div
            key="cta"
            className="absolute inset-0 flex flex-col items-center justify-center z-30"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.8, delay: 0.2 }}
          >
            {/* Logo small */}
            <motion.div
              className="flex h-14 w-14 items-center justify-center rounded-xl bg-white mb-8"
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ type: "spring", stiffness: 200, damping: 16 }}
            >
              <span className="text-2xl font-black text-black">Q</span>
            </motion.div>

            <motion.h2
              className="text-2xl sm:text-3xl font-bold text-white tracking-tight text-center"
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.3 }}
            >
              시작할 준비가 되셨나요?
            </motion.h2>

            <motion.p
              className="mt-3 text-sm text-white/40 text-center max-w-sm"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.5 }}
            >
              통계적으로 검증된 알파 엔진이 24시간 시장을 분석합니다
            </motion.p>

            <motion.button
              className="mt-8 rounded-xl bg-white px-8 py-3.5 text-sm font-semibold text-black tracking-tight hover:bg-white/90 transition-all"
              onClick={enter}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.7 }}
              whileHover={{ scale: 1.03 }}
              whileTap={{ scale: 0.97 }}
            >
              시작하기
            </motion.button>

            <motion.button
              className="mt-4 text-xs text-white/25 hover:text-white/50 transition-colors"
              onClick={enter}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 1 }}
            >
              이미 계정이 있으신가요? →
            </motion.button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Skip indicator */}
      {phase < PHASES.length - 1 && (
        <motion.p
          className="absolute bottom-8 text-[11px] text-white/15 tracking-wider z-40"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 2 }}
        >
          화면을 탭하여 건너뛰기
        </motion.p>
      )}
    </div>
  );
}
