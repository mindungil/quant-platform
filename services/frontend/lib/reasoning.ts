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

/* ── Beginner-friendly helpers ─────────────────────────────── */

/** Strip LLM thinking tags and technical noise */
export function cleanLLMResponse(text: string): string {
  if (!text) return "";
  // Remove <thinking>...</thinking> tags
  let cleaned = text.replace(/<thinking>[\s\S]*?<\/thinking>/gi, "");
  // Remove [formula=...] prefixes
  cleaned = cleaned.replace(/^\[.*?\]\s*/g, "");
  // Remove function call artifacts
  cleaned = cleaned.replace(/<function=.*?<\/function>/g, "");
  // Trim whitespace
  return cleaned.trim() || "분석 중입니다...";
}

/** Translate trading terms to beginner Korean */
export function beginnerFriendly(text: string): string {
  const replacements: [RegExp, string][] = [
    [/\bRSI\b/gi, "과매수·과매도 지표"],
    [/\bMACD\b/gi, "추세 전환 지표"],
    [/\bBollinger\s*Band/gi, "가격 변동 범위"],
    [/\bSharpe\s*(Ratio|비율)?/gi, "위험 대비 수익률"],
    [/\bMDD\b|Max\s*Drawdown/gi, "최대 손실폭"],
    [/\bADX\b/gi, "추세 강도"],
    [/\bATR\b/gi, "변동폭"],
    [/\bVWAP\b/gi, "평균 거래가"],
    [/\bEMA\b/gi, "이동평균"],
    [/\bSMA\b/gi, "단순평균"],
    [/\bStochastic\b/gi, "모멘텀"],
    [/\bOBV\b/gi, "거래량 흐름"],
    [/\bFunding\s*Rate/gi, "선물 수수료율"],
    [/\bOpen\s*Interest/gi, "미결제약정"],
    [/\bLong\/Short\b/gi, "매수/매도 비율"],
    [/\bDrawdown\b/gi, "손실폭"],
    [/\bHOLD\b/g, "관망"],
    [/\bBUY\b/g, "매수"],
    [/\bSELL\b/g, "매도"],
    [/sideways/gi, "횡보"],
    [/trending/gi, "추세"],
    [/volatile/gi, "변동성"],
    [/bullish/gi, "상승"],
    [/bearish/gi, "하락"],
  ];
  let result = text;
  for (const [pattern, replacement] of replacements) {
    result = result.replace(pattern, replacement);
  }
  return result;
}

/** Format action for beginners — icon is now a string key, rendered via icons.tsx */
export function beginnerAction(action: string | undefined): { text: string; icon: "up" | "down" | "pause"; description: string } {
  switch (action?.toUpperCase()) {
    case "BUY":
      return { text: "매수 추천", icon: "up", description: "지금 사는 것이 유리해 보여요" };
    case "SELL":
      return { text: "매도 추천", icon: "down", description: "지금 파는 것이 유리해 보여요" };
    default:
      return { text: "관망 중", icon: "pause", description: "" };
  }
}

/** Explain signal score for beginners */
export function explainScore(score: number): string {
  const abs = Math.abs(score);
  if (abs < 0.15) return "시장에 뚜렷한 방향이 없어요";
  if (abs < 0.4) return score > 0 ? "약한 상승 신호가 감지됐어요" : "약한 하락 신호가 감지됐어요";
  if (abs < 0.7) return score > 0 ? "상승 가능성이 보여요" : "하락 가능성이 보여요";
  return score > 0 ? "강한 상승 신호예요!" : "강한 하락 신호예요!";
}

/** Explain regime for beginners */
export function explainRegime(regime: string): string {
  const r = (regime || "").toLowerCase();
  if (r.includes("trend")) return "시장이 한 방향으로 움직이고 있어요";
  if (r.includes("sideways") || r.includes("ranging")) return "시장이 큰 변화 없이 옆으로 움직여요";
  if (r.includes("volatile")) return "시장 변동이 큰 상태예요. 조심하세요";
  return "시장을 분석하고 있어요";
}

/** Translate indicator name to beginner-friendly Korean */
export function beginnerIndicatorName(raw: string): string {
  const map: Record<string, string> = {
    adx_filter: "추세 강도", adx_14: "추세 강도", adx_strength: "추세 강도",
    stochastic: "모멘텀", stochastic_k: "모멘텀", stochastic_d: "모멘텀",
    stochastic_level: "모멘텀", stoch_momentum: "모멘텀",
    sma_20: "단순평균", sma_distance: "단순평균",
    vwap: "평균 거래가", vwap_distance: "평균 거래가", vwap_reversion: "평균 거래가",
    macd: "추세 전환", macd_histogram: "추세 전환", macd_crossover: "추세 전환",
    rsi: "과매수·과매도", rsi_14: "과매수·과매도", rsi_level: "과매수·과매도",
    rsi_extreme: "과매수·과매도", rsi_momentum: "과매수·과매도",
    bollinger: "가격 변동 범위", bollinger_pctb: "가격 변동 범위",
    bb_contrarian: "가격 변동 범위", bb_width: "가격 변동 범위", bb_width_squeeze: "가격 변동 범위",
    ema_9: "이동평균", ema_21: "이동평균", ema_50: "이동평균",
    ema_alignment: "이동평균", ema_cross_9_21: "이동평균", ema_cross_21_50: "이동평균",
    ema_spread: "이동평균",
    fear_greed: "시장 심리", fear_greed_index: "시장 심리",
    funding_rate_signal: "선물 수수료", funding_rate_extreme: "선물 수수료",
    open_interest_trend: "미결제약정", long_short_ratio: "매수/매도 비율",
    taker_buy_sell: "매수/매도 비율", derivatives_sentiment: "파생상품 심리",
    btc_dominance: "비트코인 점유율", news_sentiment: "뉴스 심리", onchain_score: "온체인 지표",
    macro_risk: "거시경제 위험", macro_risk_score: "거시경제 위험",
    obv_momentum: "거래량 흐름", volume_volatility: "거래량 변동",
    atr_relative: "변동폭", atr_percentile: "변동폭",
    range_expansion: "가격 확장", squeeze_indicator: "에너지 축적",
    trend_consistency: "추세 일관성", price_momentum_short: "단기 모멘텀",
    price_momentum_medium: "중기 모멘텀", price_vs_range: "가격 위치",
    formula_confidence: "신뢰도",
  };
  return map[raw] || raw.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

/** Translate indicator value to beginner-friendly strength label */
export function beginnerIndicatorStrength(value: number): string {
  const abs = Math.abs(value * 100);
  if (abs >= 80) return "매우 강함";
  if (abs >= 50) return "강함";
  if (abs >= 30) return "보통";
  return "약함";
}
