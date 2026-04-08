/**
 * Parse reasoning string into structured data.
 * Handles: JSON structured, plain text with [formula=...] prefix, plain text, null/empty.
 */
export interface StructuredReasoning {
  summary: string;
  asset: string;
  score: number;
  direction: string;  // 매수/매도
  action: string;     // BUY/SELL/HOLD
  strength: string;   // 강함/보통/약함
  regime: string;
  regime_description: string;
  bullish_indicators: Array<{ name: string; value: number }>;
  bearish_indicators: Array<{ name: string; value: number }>;
  conflicts: string[];
  memory_refs: number;
  formula: string | null;
  strategy: string | null;
}

export interface ParsedReasoning {
  structured: StructuredReasoning | null;
  text: string;
}

export function parseReasoning(raw: string | null | undefined): ParsedReasoning {
  if (!raw) return { structured: null, text: "" };

  // Only attempt JSON parse if it starts with {
  if (raw.trimStart().startsWith("{")) {
    try {
      const parsed = JSON.parse(raw);
      // Format 1: {"structured": {...}, "text": "..."}
      if (parsed.structured && typeof parsed.structured === "object") {
        return {
          structured: parsed.structured,
          text: parsed.text || parsed.structured.summary || "",
        };
      }
      // JSON but no structured field — extract text if present, otherwise treat as plain text
      if (parsed.text && typeof parsed.text === "string") {
        return { structured: null, text: parsed.text };
      }
      // Some other JSON object we don't recognize — treat as plain text
    } catch {
      // Starts with { but isn't valid JSON — treat as plain text
    }
  }

  // Strip [formula=...] prefix
  const cleaned = raw.replace(/^\[.*?\]\s*/, "");
  return { structured: null, text: cleaned };
}

/** Map action to Korean label + color */
export function actionLabel(action: string): { text: string; color: string } {
  switch (action?.toUpperCase()) {
    case "BUY":
      return { text: "매수", color: "text-emerald-400 bg-emerald-500/15" };
    case "SELL":
      return { text: "매도", color: "text-red-400 bg-red-500/15" };
    default:
      return { text: "관망", color: "text-zinc-400 bg-white/[0.08]" };
  }
}

/** Format raw indicator names to human-readable labels */
export function formatIndicatorName(raw: string): string {
  const map: Record<string, string> = {
    adx_filter: "ADX", adx_14: "ADX", adx_strength: "ADX",
    stochastic: "Stochastic", stochastic_k: "Stoch K", stochastic_d: "Stoch D",
    stochastic_level: "Stochastic", stoch_momentum: "Stoch Momentum",
    sma_20: "SMA 20", sma_distance: "SMA",
    vwap: "VWAP", vwap_distance: "VWAP", vwap_reversion: "VWAP Rev",
    macd: "MACD", macd_histogram: "MACD Hist", macd_crossover: "MACD Cross",
    rsi: "RSI", rsi_14: "RSI", rsi_level: "RSI", rsi_extreme: "RSI Extreme",
    rsi_momentum: "RSI Mom",
    bollinger: "Bollinger", bollinger_pctb: "BB %B", bb_contrarian: "BB Rev",
    bb_width: "BB Width", bb_width_squeeze: "BB Squeeze",
    ema_9: "EMA 9", ema_21: "EMA 21", ema_50: "EMA 50",
    ema_alignment: "EMA Align", ema_cross_9_21: "EMA 9/21", ema_cross_21_50: "EMA 21/50",
    ema_spread: "EMA Spread",
    fear_greed: "Fear & Greed", fear_greed_index: "Fear & Greed",
    funding_rate_signal: "Funding Rate", funding_rate_extreme: "FR Extreme",
    open_interest_trend: "Open Interest", long_short_ratio: "Long/Short",
    taker_buy_sell: "Taker Ratio", derivatives_sentiment: "Deriv Sent",
    btc_dominance: "BTC Dom", news_sentiment: "News", onchain_score: "On-Chain",
    macro_risk: "Macro Risk", macro_risk_score: "Macro Risk",
    obv_momentum: "OBV", volume_volatility: "Vol/Vol",
    atr_relative: "ATR Rel", atr_percentile: "ATR %",
    range_expansion: "Range Exp", squeeze_indicator: "Squeeze",
    trend_consistency: "Trend", price_momentum_short: "Mom Short",
    price_momentum_medium: "Mom Med", price_vs_range: "Price Range",
    formula_confidence: "Confidence",
  };
  return map[raw] || raw.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

/** Clean reasoning for display — always returns human-readable text, never JSON */
export function cleanReasoning(raw: string): string {
  const { structured, text } = parseReasoning(raw);
  if (structured) return structured.summary;
  return text || "분석 중...";
}

/** Relative time display in Korean */
export function timeAgo(timestamp: string): string {
  const now = Date.now();
  const then = new Date(timestamp).getTime();
  const diff = Math.floor((now - then) / 1000);
  if (diff < 60) return "방금 전";
  if (diff < 3600) return `${Math.floor(diff / 60)}분 전`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}시간 전`;
  return `${Math.floor(diff / 86400)}일 전`;
}
