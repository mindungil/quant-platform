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

/** Format strategy/formula description to Korean */
export function formatStrategyDescription(raw: string): string {
  if (!raw) return "";
  const replacements: [RegExp, string][] = [
    [/Regime-aware weighted blend of all indicators.*$/i, "모든 지표를 레짐에 맞게 가중 분석"],
    [/Multi-factor ensemble combining \d+\+ decorrelated alpha factors/i, "45+ 비상관 알파 팩터 앙상블"],
    [/EMA\((\d+)\) vs EMA\((\d+)\) crossover with ATR-normalized distance/i, "EMA $1/$2 교차 모멘텀 (ATR 정규화)"],
    [/MACD histogram direction.*$/i, "MACD 히스토그램 방향성 분석"],
    [/Bollinger.*mean reversion.*$/i, "볼린저 밴드 평균 회귀"],
    [/VWAP.*reversion.*$/i, "VWAP 평균 회귀"],
    [/RSI.*divergence.*$/i, "RSI 다이버전스"],
    [/Volatility breakout.*$/i, "변동성 돌파"],
    [/Default fallback\.?/gi, ""],
    [/stochastic momentum.*$/i, "스토캐스틱 모멘텀"],
    [/momentum.*scoring.*$/i, "모멘텀 스코어링"],
    [/mean reversion.*$/i, "평균 회귀 분석"],
    [/trend following.*$/i, "추세 추종 분석"],
    [/breakout detection.*$/i, "돌파 감지"],
    [/volume.*weighted.*$/i, "거래량 가중 분석"],
    [/ATR.*trailing.*$/i, "ATR 추적 분석"],
    [/fear.*greed.*$/i, "공포/탐욕 지수 분석"],
    [/funding rate.*$/i, "펀딩 레이트 분석"],
    [/on-?chain.*$/i, "온체인 데이터 분석"],
    [/sentiment.*analysis.*$/i, "심리 분석"],
    [/support.*resistance.*$/i, "지지/저항 분석"],
    [/fibonacci.*$/i, "피보나치 분석"],
    [/ichimoku.*$/i, "일목균형표 분석"],
    [/price action.*$/i, "프라이스 액션"],
    [/order flow.*$/i, "주문 흐름 분석"],
    [/liquidity.*$/i, "유동성 분석"],
    [/correlation.*$/i, "상관관계 분석"],
    [/volatility.*analysis.*$/i, "변동성 분석"],
    [/weighted blend.*$/i, "가중 혼합 분석"],
    [/ensemble.*$/i, "앙상블 분석"],
    [/adaptive.*$/i, "적응형 분석"],
  ];
  let result = raw;
  for (const [pattern, replacement] of replacements) {
    result = result.replace(pattern, replacement);
  }
  return result.trim() || raw;
}

/** Format regime label to Korean */
export function formatRegime(regime: string): string {
  if (!regime) return "분석 중";
  const parts = regime.toLowerCase().split(/[_\/\s]+/);
  const trend = parts.find(p => ["trending", "sideways", "ranging"].includes(p));
  const vol = parts.find(p => ["high", "normal", "low"].includes(p));
  const mom = parts.find(p => ["bullish", "bearish", "neutral"].includes(p));

  const trendKo = trend === "trending" ? "추세" : trend === "sideways" || trend === "ranging" ? "횡보" : "";
  const volKo = vol === "high" ? "고변동" : vol === "low" ? "저변동" : "보통";
  const momKo = mom === "bullish" ? "상승" : mom === "bearish" ? "하락" : "중립";

  const result = [trendKo, volKo, momKo].filter(Boolean).join(" · ");
  return result || "분석 중";
}

/** Format confidence as a visual level */
export function formatConfidence(confidence: number): { text: string; level: "high" | "medium" | "low" } {
  if (confidence >= 0.6) return { text: "높음", level: "high" };
  if (confidence >= 0.3) return { text: "보통", level: "medium" };
  return { text: "낮음", level: "low" };
}

/** Format regime fit */
export function formatRegimeFit(fit: string): string {
  if (!fit) return "";
  if (fit.includes("높음") || fit.includes("high")) return "적합";
  if (fit.includes("낮음") || fit.includes("low")) return "부적합";
  return "보통";
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
