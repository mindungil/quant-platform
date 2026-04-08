/**
 * Parse reasoning string into structured data.
 * Handles: JSON structured, plain text with [formula=...] prefix, plain text.
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

export function parseReasoning(raw: string): ParsedReasoning {
  if (!raw) return { structured: null, text: "" };

  try {
    const parsed = JSON.parse(raw);
    if (parsed.structured) {
      return {
        structured: parsed.structured,
        text: parsed.text || parsed.structured.summary || "",
      };
    }
    // JSON but no structured field
    if (parsed.text) {
      return { structured: null, text: parsed.text };
    }
  } catch {
    // Not JSON — plain text
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

/** Clean reasoning for display — always returns human-readable text */
export function cleanReasoning(raw: string): string {
  const { structured, text } = parseReasoning(raw);
  if (structured) return structured.summary;
  return text || "분석 중...";
}
