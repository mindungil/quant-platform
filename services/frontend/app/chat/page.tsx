"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { gatewayFetch } from "../../lib/api";
import { cleanReasoning } from "../../lib/reasoning";

/* ── Types ──────────────────────────────────────────────────── */

type ToolCall = {
  tool_name: string;
  arguments: Record<string, unknown>;
  result?: string;
  error?: string;
};

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  tool_calls?: ToolCall[];
  provider?: string;
  loop_count?: number;
  elapsed_ms?: number;
  created_at?: string;
};

type Conversation = {
  conversation_id: string;
  title: string;
  updated_at?: string;
};

/* ── Tool name mapping ──────────────────────────────────────── */

const TOOL_LABELS: Record<string, string> = {
  get_market_data: "시세 조회",
  get_features: "기술 지표 분석",
  detect_regime: "시장 상태 판단",
  search_memory: "과거 기록 검색",
  get_risk_assessment: "리스크 평가",
  place_order: "주문 실행",
  get_trading_rules: "매매 규칙 확인",
  get_formula_guide: "공식 가이드 조회",
  full_market_analysis: "종합 분석",
};

function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? "처리";
}

/* ── Animation variants ─────────────────────────────────────── */

const springTransition = { type: "spring", stiffness: 400, damping: 30 };
const gentleSpring = { type: "spring", stiffness: 260, damping: 28 };

const messageVariants = {
  hidden: { opacity: 0, y: 16, scale: 0.97 },
  visible: { opacity: 1, y: 0, scale: 1, transition: gentleSpring },
};

const pillVariants = {
  hidden: { opacity: 0, scale: 0.8, y: 4 },
  visible: { opacity: 1, scale: 1, y: 0 },
};

const sidebarVariants = {
  open: { width: 280, opacity: 1, transition: gentleSpring },
  closed: { width: 0, opacity: 0, transition: { ...gentleSpring, opacity: { duration: 0.15 } } },
};

/* ── Elapsed time formatter ─────────────────────────────────── */

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${Math.round(ms / 100) / 10}초`;
  return `${(ms / 1000).toFixed(1)}초`;
}

/* ── Animated loading dots ──────────────────────────────────── */

function LoadingDots() {
  return (
    <span className="inline-flex items-center gap-[3px]">
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="inline-block h-[6px] w-[6px] rounded-full"
          style={{
            background: "linear-gradient(135deg, #ffffff, #a1a1aa)",
          }}
          animate={{
            y: [0, -6, 0],
            opacity: [0.4, 1, 0.4],
          }}
          transition={{
            duration: 0.9,
            repeat: Infinity,
            delay: i * 0.15,
            ease: "easeInOut",
          }}
        />
      ))}
    </span>
  );
}

/* ── Tool pill popover ──────────────────────────────────────── */

function ToolPill({
  tc,
  index,
  isLoading,
}: {
  tc: ToolCall;
  index: number;
  isLoading: boolean;
}) {
  const [showPopover, setShowPopover] = useState(false);
  const hasError = !!tc.error;
  const label = isLoading ? `${toolLabel(tc.tool_name)} 중...` : toolLabel(tc.tool_name);

  return (
    <motion.div
      className="relative"
      variants={pillVariants}
      transition={{ ...springTransition, delay: index * 0.06 }}
    >
      <button
        onClick={() => setShowPopover((p) => !p)}
        onMouseLeave={() => setShowPopover(false)}
        className={`
          inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium
          transition-all duration-200
          ${
            hasError
              ? "bg-red-500/10 text-red-400 hover:bg-red-500/20"
              : "bg-white/[0.08] text-zinc-300 hover:bg-white/[0.12] hover:text-white"
          }
        `}
      >
        {isLoading ? (
          <motion.span
            className="inline-block h-3 w-3 rounded-full border-[1.5px] border-neutral-500 border-t-transparent"
            animate={{ rotate: 360 }}
            transition={{ duration: 0.8, repeat: Infinity, ease: "linear" }}
          />
        ) : hasError ? (
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.5" />
            <path d="M5.5 5.5l5 5M10.5 5.5l-5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        ) : (
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
            <path d="M3.5 8.5l3 3 6-7" stroke="#34d399" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
        {label}
      </button>

      {/* Popover with details */}
      <AnimatePresence>
        {showPopover && !isLoading && (
          <motion.div
            initial={{ opacity: 0, y: 4, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 4, scale: 0.95 }}
            transition={{ duration: 0.15 }}
            className="absolute left-0 top-full z-50 mt-2 w-72 rounded-lg border border-neutral-700/50 bg-neutral-900/95 p-3 shadow-xl backdrop-blur-sm"
          >
            <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-neutral-500">
              {tc.tool_name}
            </div>
            {Object.keys(tc.arguments).length > 0 && (
              <pre className="mb-2 max-h-20 overflow-auto rounded bg-neutral-950/80 p-2 text-[11px] leading-relaxed text-neutral-400">
                {JSON.stringify(tc.arguments, null, 2)}
              </pre>
            )}
            {tc.result && (() => {
              // Try to show friendly summary for structured reasoning results
              try {
                const parsed = JSON.parse(tc.result);
                if (parsed.structured?.summary) {
                  return (
                    <div className="rounded bg-neutral-950/80 p-2 text-[11px] leading-relaxed text-neutral-300">
                      {parsed.structured.summary}
                    </div>
                  );
                }
              } catch {
                // Not JSON or no structured field — check if cleanReasoning can parse it
                const cleaned = cleanReasoning(tc.result);
                if (cleaned !== tc.result && cleaned !== "분석 중...") {
                  return (
                    <div className="rounded bg-neutral-950/80 p-2 text-[11px] leading-relaxed text-neutral-300">
                      {cleaned}
                    </div>
                  );
                }
              }
              return (
                <pre className="max-h-28 overflow-auto rounded bg-neutral-950/80 p-2 text-[11px] leading-relaxed text-neutral-400">
                  {tc.result.length > 400 ? tc.result.slice(0, 400) + "..." : tc.result}
                </pre>
              );
            })()}
            {tc.error && (
              <p className="text-xs text-red-400">{tc.error}</p>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

/* ── Main Page ──────────────────────────────────────────────── */

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [showSidebar, setShowSidebar] = useState(true);
  const [inputFocused, setInputFocused] = useState(false);
  const messagesEnd = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const scrollToBottom = useCallback(() => {
    messagesEnd.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  useEffect(() => {
    loadConversations();
  }, []);

  async function loadConversations() {
    try {
      const data = await gatewayFetch("/conversations");
      setConversations(Array.isArray(data) ? data : []);
    } catch {
      /* ignore */
    }
  }

  async function loadConversation(convId: string) {
    try {
      const data = await gatewayFetch(`/conversations/${convId}/messages`);
      const msgs: Message[] = (Array.isArray(data) ? data : []).map(
        (m: Record<string, unknown>) => ({
          id: m.message_id as string,
          role: m.role as "user" | "assistant",
          content: m.content as string,
          tool_calls: m.tool_calls as ToolCall[] | undefined,
          created_at: m.created_at as string | undefined,
        })
      );
      setMessages(msgs);
      setConversationId(convId);
    } catch {
      /* ignore */
    }
  }

  async function sendMessage() {
    const text = input.trim();
    if (!text || loading) return;

    const userMsg: Message = {
      id: `user-${Date.now()}`,
      role: "user",
      content: text,
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    try {
      const body: Record<string, unknown> = { message: text };
      if (conversationId) body.conversation_id = conversationId;

      const data = await gatewayFetch("/chat", {
        method: "POST",
        body: JSON.stringify(body),
      });

      if (!conversationId && data.conversation_id) {
        setConversationId(data.conversation_id);
        loadConversations();
      }

      const assistantMsg: Message = {
        id: data.message_id || `asst-${Date.now()}`,
        role: "assistant",
        content: data.text || "",
        tool_calls: data.tool_calls || [],
        elapsed_ms: data.elapsed_ms,
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err) {
      const errMsg: Message = {
        id: `err-${Date.now()}`,
        role: "assistant",
        content: `오류가 발생했습니다: ${err instanceof Error ? err.message : "알 수 없는 오류"}`,
      };
      setMessages((prev) => [...prev, errMsg]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  function startNewConversation() {
    setConversationId(null);
    setMessages([]);
    inputRef.current?.focus();
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }

  const suggestions = [
    { text: "BTC 현재 상황 분석해줘", icon: "📊" },
    { text: "ETH 매수 타이밍이야?", icon: "⏱" },
    { text: "포트폴리오 현황 보여줘", icon: "💼" },
    { text: "모멘텀 공식 백테스트 해봐", icon: "🧪" },
  ];

  return (
    <div className="flex h-[calc(100vh-64px)] bg-neutral-950">
      {/* ── Sidebar ─────────────────────────────────────────── */}
      <AnimatePresence initial={false}>
        {showSidebar && (
          <motion.aside
            variants={sidebarVariants}
            initial="closed"
            animate="open"
            exit="closed"
            className="flex flex-col overflow-hidden border-r border-neutral-800/60 bg-zinc-950"
          >
            <div className="flex items-center justify-between p-4">
              <span className="text-xs font-semibold uppercase tracking-wider text-neutral-500">
                대화 목록
              </span>
              <motion.button
                onClick={startNewConversation}
                whileHover={{ scale: 1.04 }}
                whileTap={{ scale: 0.96 }}
                className="group relative rounded-lg btn-gradient px-3 py-1.5 text-xs font-semibold text-white transition-shadow"
              >
                <span className="relative z-10">+ 새 대화</span>
              </motion.button>
            </div>

            <div className="flex-1 overflow-y-auto px-2 pb-4">
              {conversations.map((conv) => (
                <motion.button
                  key={conv.conversation_id}
                  onClick={() => loadConversation(conv.conversation_id)}
                  whileHover={{ x: 2 }}
                  transition={{ duration: 0.15 }}
                  className={`mb-0.5 w-full rounded-lg px-3 py-2.5 text-left transition-colors ${
                    conversationId === conv.conversation_id
                      ? "bg-neutral-800/80 text-white"
                      : "text-neutral-400 hover:bg-neutral-800/40 hover:text-neutral-200"
                  }`}
                >
                  <div className="truncate text-sm">
                    {conv.title || "새 대화"}
                  </div>
                  {conv.updated_at && (
                    <div className="mt-0.5 text-[10px] text-neutral-500">
                      {new Date(conv.updated_at).toLocaleDateString("ko-KR")}
                    </div>
                  )}
                </motion.button>
              ))}
              {conversations.length === 0 && (
                <p className="px-3 py-8 text-center text-xs text-neutral-500">
                  대화 기록이 없습니다
                </p>
              )}
            </div>
          </motion.aside>
        )}
      </AnimatePresence>

      {/* ── Main chat area ──────────────────────────────────── */}
      <div className="relative flex flex-1 flex-col">
        {/* Header */}
        <div className="flex items-center gap-3 border-b border-neutral-800/40 px-5 py-3">
          <motion.button
            onClick={() => setShowSidebar(!showSidebar)}
            whileHover={{ scale: 1.1 }}
            whileTap={{ scale: 0.9 }}
            className="rounded-lg p-2 text-neutral-500 transition-colors hover:bg-neutral-800/60 hover:text-white"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M3 12h18M3 6h18M3 18h18" />
            </svg>
          </motion.button>
          <h1 className="text-sm font-bold text-white tracking-tight text-glow">
            AI 트레이딩 에이전트
          </h1>
        </div>

        {/* Messages area */}
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-5 py-6">
            {/* ── Welcome screen ──────────────────────────────── */}
            {messages.length === 0 && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: 0.6 }}
                className="flex h-[calc(100vh-250px)] flex-col items-center justify-center text-center"
              >
                <motion.div
                  initial={{ scale: 0.8, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={{ ...gentleSpring, delay: 0.1 }}
                  className="mb-8"
                >
                  <span
                    className="inline-block bg-clip-text text-6xl font-bold text-transparent text-glow-strong"
                    style={{
                      backgroundImage: "linear-gradient(135deg, #fafafa, #a1a1aa, #fafafa)",
                      backgroundSize: "200% 200%",
                      animation: "gradientShift 4s ease infinite",
                    }}
                  >
                    Quant AI
                  </span>
                </motion.div>
                <motion.p
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.25 }}
                  className="mb-10 max-w-md text-base leading-relaxed text-neutral-400"
                >
                  시장 분석, 매매 실행, 공식 평가를 자연어로 요청하세요.
                  <br />
                  에이전트가 필요한 도구를 자동으로 호출합니다.
                </motion.p>
                <motion.div
                  initial="hidden"
                  animate="visible"
                  variants={{
                    hidden: {},
                    visible: { transition: { staggerChildren: 0.08 } },
                  }}
                  className="grid w-full max-w-lg grid-cols-2 gap-3"
                >
                  {suggestions.map((s) => (
                    <motion.button
                      key={s.text}
                      variants={{
                        hidden: { opacity: 0, y: 12 },
                        visible: { opacity: 1, y: 0 },
                      }}
                      whileHover={{
                        scale: 1.02,
                        boxShadow: "0 0 24px rgba(255, 255, 255, 0.08)",
                      }}
                      whileTap={{ scale: 0.98 }}
                      onClick={() => {
                        setInput(s.text);
                        inputRef.current?.focus();
                      }}
                      className="rounded-xl border border-neutral-800/60 bg-neutral-900/50 px-4 py-3.5 text-left text-sm text-neutral-300 transition-colors hover:border-neutral-600 hover:text-white"
                    >
                      <span className="mr-2">{s.icon}</span>
                      {s.text}
                    </motion.button>
                  ))}
                </motion.div>
              </motion.div>
            )}

            {/* ── Message list ────────────────────────────────── */}
            <AnimatePresence initial={false}>
              {messages.map((msg) => (
                <motion.div
                  key={msg.id}
                  variants={messageVariants}
                  initial="hidden"
                  animate="visible"
                  layout
                  className={`mb-5 ${msg.role === "user" ? "flex justify-end" : ""}`}
                >
                  {msg.role === "user" ? (
                    /* ── User bubble ──────────────────── */
                    <div className="max-w-[75%] rounded-2xl rounded-br-md bg-white/[0.10] px-5 py-3 text-[15px] leading-relaxed text-white shadow-[0_2px_12px_rgba(255,255,255,0.06)]">
                      {msg.content}
                    </div>
                  ) : (
                    /* ── Assistant message ────────────── */
                    <div className="max-w-[90%]">
                      {/* Tool call pills */}
                      {msg.tool_calls && msg.tool_calls.length > 0 && (
                        <motion.div
                          initial="hidden"
                          animate="visible"
                          variants={{
                            hidden: {},
                            visible: { transition: { staggerChildren: 0.06 } },
                          }}
                          className="mb-3 flex flex-wrap gap-1.5"
                        >
                          {msg.tool_calls.map((tc, idx) => (
                            <ToolPill
                              key={`${msg.id}-tool-${idx}`}
                              tc={tc}
                              index={idx}
                              isLoading={false}
                            />
                          ))}
                        </motion.div>
                      )}

                      {/* Text content */}
                      <div className="border-l-2 border-white/[0.15] pl-4">
                        <div className="whitespace-pre-wrap text-[15px] leading-[1.75] text-neutral-200">
                          {msg.content}
                        </div>
                        {/* Subtle elapsed time */}
                        {msg.elapsed_ms !== undefined && msg.elapsed_ms > 0 && (
                          <div className="mt-2 text-[11px] text-neutral-500">
                            {formatElapsed(msg.elapsed_ms)}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </motion.div>
              ))}
            </AnimatePresence>

            {/* ── Loading indicator ────────────────────────────── */}
            <AnimatePresence>
              {loading && (
                <motion.div
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -4 }}
                  className="mb-5"
                >
                  <div className="inline-flex items-center gap-3 border-l-2 border-neutral-700/50 pl-4 text-[15px] text-neutral-400">
                    <LoadingDots />
                    <span>분석 중</span>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            <div ref={messagesEnd} />
          </div>
        </div>

        {/* ── Input area ────────────────────────────────────── */}
        <div className="relative border-t border-neutral-800/30">
          {/* Blur backdrop */}
          <div className="absolute inset-0 bg-neutral-950/80 backdrop-blur-xl" />

          <div className="relative mx-auto max-w-3xl px-5 py-5">
            <motion.div
              animate={{
                boxShadow: inputFocused
                  ? "0 0 0 2px rgba(255, 255, 255, 0.15), 0 4px 24px rgba(0,0,0,0.3)"
                  : "0 0 0 1px rgba(64, 64, 64, 0.5), 0 2px 12px rgba(0,0,0,0.2)",
              }}
              transition={{ duration: 0.2 }}
              className="flex items-end gap-3 rounded-2xl bg-neutral-900/90 px-4 py-3"
            >
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                onFocus={() => setInputFocused(true)}
                onBlur={() => setInputFocused(false)}
                placeholder="메시지를 입력하세요..."
                rows={1}
                className="flex-1 resize-none bg-transparent text-[15px] text-white placeholder-neutral-600 outline-none"
                style={{
                  minHeight: "28px",
                  maxHeight: "140px",
                }}
                onInput={(e) => {
                  const target = e.target as HTMLTextAreaElement;
                  target.style.height = "auto";
                  target.style.height = `${Math.min(target.scrollHeight, 140)}px`;
                }}
              />
              <motion.button
                onClick={sendMessage}
                disabled={loading || !input.trim()}
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
                className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-xl bg-white text-black transition-opacity disabled:opacity-20"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M5 12h14M12 5l7 7-7 7" />
                </svg>
              </motion.button>
            </motion.div>
            <p className="mt-2 text-center text-[11px] text-neutral-500">
              Shift+Enter로 줄바꿈
            </p>
          </div>
        </div>
      </div>

      {/* ── Global keyframe styles ─────────────────────────── */}
      <style jsx global>{`
        @keyframes gradientShift {
          0%, 100% { background-position: 0% 50%; }
          50% { background-position: 100% 50%; }
        }
      `}</style>
    </div>
  );
}
