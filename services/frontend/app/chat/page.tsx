"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { gatewayFetch } from "../../lib/api";

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

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [showSidebar, setShowSidebar] = useState(true);
  const [expandedTools, setExpandedTools] = useState<Set<string>>(new Set());
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
        provider: data.provider,
        loop_count: data.loop_count,
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

  function toggleTool(id: string) {
    setExpandedTools((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }

  return (
    <div className="flex h-[calc(100vh-64px)]">
      {/* Sidebar */}
      <AnimatePresence>
        {showSidebar && (
          <motion.aside
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: 260, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            className="flex flex-col border-r border-neutral-800 bg-neutral-950"
          >
            <div className="flex items-center justify-between border-b border-neutral-800 p-3">
              <span className="text-sm font-medium text-neutral-300">
                대화 목록
              </span>
              <button
                onClick={startNewConversation}
                className="rounded bg-white px-2.5 py-1 text-xs font-medium text-black transition-colors hover:bg-neutral-200"
              >
                + 새 대화
              </button>
            </div>
            <div className="flex-1 overflow-y-auto">
              {conversations.map((conv) => (
                <button
                  key={conv.conversation_id}
                  onClick={() => loadConversation(conv.conversation_id)}
                  className={`w-full border-b border-neutral-900 px-3 py-2.5 text-left text-sm transition-colors ${
                    conversationId === conv.conversation_id
                      ? "bg-neutral-800 text-white"
                      : "text-neutral-400 hover:bg-neutral-900 hover:text-neutral-200"
                  }`}
                >
                  <div className="truncate">
                    {conv.title || "새 대화"}
                  </div>
                  {conv.updated_at && (
                    <div className="mt-0.5 text-[10px] text-neutral-600">
                      {new Date(conv.updated_at).toLocaleDateString("ko-KR")}
                    </div>
                  )}
                </button>
              ))}
              {conversations.length === 0 && (
                <p className="p-4 text-center text-xs text-neutral-600">
                  대화 기록이 없습니다
                </p>
              )}
            </div>
          </motion.aside>
        )}
      </AnimatePresence>

      {/* Main chat area */}
      <div className="flex flex-1 flex-col">
        {/* Header */}
        <div className="flex items-center gap-3 border-b border-neutral-800 px-4 py-2.5">
          <button
            onClick={() => setShowSidebar(!showSidebar)}
            className="rounded p-1.5 text-neutral-500 transition-colors hover:bg-neutral-800 hover:text-white"
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M3 12h18M3 6h18M3 18h18" />
            </svg>
          </button>
          <h1 className="text-sm font-medium text-white">
            AI 트레이딩 에이전트
          </h1>
          <span className="text-xs text-neutral-600">
            시장 분석 / 매매 실행 / 공식 관리
          </span>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-4">
          {messages.length === 0 && (
            <div className="flex h-full flex-col items-center justify-center text-center">
              <div className="mb-6 text-4xl font-light text-neutral-700">
                Q
              </div>
              <h2 className="mb-2 text-lg font-medium text-neutral-300">
                퀀트 AI 에이전트
              </h2>
              <p className="mb-6 max-w-md text-sm text-neutral-500">
                시장 분석, 매매 실행, 공식 평가를 자연어로 요청하세요.
                에이전트가 필요한 도구를 자동으로 호출합니다.
              </p>
              <div className="grid max-w-lg grid-cols-2 gap-2">
                {[
                  "BTC 현재 상황 분석해줘",
                  "ETH 매수 타이밍이야?",
                  "포트폴리오 현황 보여줘",
                  "모멘텀 공식 백테스트 해봐",
                ].map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => {
                      setInput(suggestion);
                      inputRef.current?.focus();
                    }}
                    className="rounded border border-neutral-800 px-3 py-2 text-left text-xs text-neutral-400 transition-colors hover:border-neutral-600 hover:text-white"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg) => (
            <motion.div
              key={msg.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              className={`mb-4 ${
                msg.role === "user" ? "flex justify-end" : ""
              }`}
            >
              {msg.role === "user" ? (
                <div className="max-w-[70%] rounded-2xl rounded-br-sm bg-white px-4 py-2.5 text-sm text-black">
                  {msg.content}
                </div>
              ) : (
                <div className="max-w-[85%]">
                  {/* Tool calls */}
                  {msg.tool_calls && msg.tool_calls.length > 0 && (
                    <div className="mb-2 space-y-1">
                      {msg.tool_calls.map((tc, idx) => {
                        const toolId = `${msg.id}-tool-${idx}`;
                        const isExpanded = expandedTools.has(toolId);
                        return (
                          <div
                            key={toolId}
                            className="rounded border border-neutral-800 bg-neutral-950"
                          >
                            <button
                              onClick={() => toggleTool(toolId)}
                              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs"
                            >
                              <span className="font-mono text-emerald-500">
                                {tc.error ? "!" : "\u2713"}
                              </span>
                              <span className="font-mono text-neutral-400">
                                {tc.tool_name}
                              </span>
                              <span className="text-neutral-600">
                                {isExpanded ? "\u25B2" : "\u25BC"}
                              </span>
                            </button>
                            <AnimatePresence>
                              {isExpanded && (
                                <motion.div
                                  initial={{ height: 0, opacity: 0 }}
                                  animate={{ height: "auto", opacity: 1 }}
                                  exit={{ height: 0, opacity: 0 }}
                                  className="overflow-hidden"
                                >
                                  <div className="border-t border-neutral-800 px-3 py-2">
                                    <div className="mb-1 text-[10px] font-medium uppercase text-neutral-600">
                                      Arguments
                                    </div>
                                    <pre className="mb-2 max-h-24 overflow-auto text-[11px] text-neutral-500">
                                      {JSON.stringify(
                                        tc.arguments,
                                        null,
                                        2
                                      )}
                                    </pre>
                                    {tc.result && (
                                      <>
                                        <div className="mb-1 text-[10px] font-medium uppercase text-neutral-600">
                                          Result
                                        </div>
                                        <pre className="max-h-32 overflow-auto text-[11px] text-neutral-500">
                                          {tc.result.length > 500
                                            ? tc.result.slice(0, 500) + "..."
                                            : tc.result}
                                        </pre>
                                      </>
                                    )}
                                    {tc.error && (
                                      <p className="text-xs text-red-400">
                                        {tc.error}
                                      </p>
                                    )}
                                  </div>
                                </motion.div>
                              )}
                            </AnimatePresence>
                          </div>
                        );
                      })}
                    </div>
                  )}
                  {/* Text response */}
                  <div className="rounded-2xl rounded-bl-sm bg-neutral-900 px-4 py-2.5 text-sm text-neutral-200">
                    <div className="whitespace-pre-wrap">{msg.content}</div>
                    {(msg.provider || msg.elapsed_ms) && (
                      <div className="mt-2 flex gap-3 border-t border-neutral-800 pt-1.5 text-[10px] text-neutral-600">
                        {msg.provider && <span>{msg.provider}</span>}
                        {msg.loop_count !== undefined && msg.loop_count > 0 && (
                          <span>{msg.loop_count}회 루프</span>
                        )}
                        {msg.elapsed_ms !== undefined && msg.elapsed_ms > 0 && (
                          <span>
                            {msg.elapsed_ms < 1000
                              ? `${Math.round(msg.elapsed_ms)}ms`
                              : `${(msg.elapsed_ms / 1000).toFixed(1)}s`}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </motion.div>
          ))}

          {loading && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="mb-4"
            >
              <div className="inline-flex items-center gap-2 rounded-2xl bg-neutral-900 px-4 py-2.5 text-sm text-neutral-500">
                <span className="flex gap-1">
                  <span className="animate-bounce">.</span>
                  <span
                    className="animate-bounce"
                    style={{ animationDelay: "0.1s" }}
                  >
                    .
                  </span>
                  <span
                    className="animate-bounce"
                    style={{ animationDelay: "0.2s" }}
                  >
                    .
                  </span>
                </span>
                분석 중
              </div>
            </motion.div>
          )}

          <div ref={messagesEnd} />
        </div>

        {/* Input */}
        <div className="border-t border-neutral-800 p-4">
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="메시지를 입력하세요... (Shift+Enter: 줄바꿈)"
              rows={1}
              className="flex-1 resize-none rounded-xl border border-neutral-700 bg-neutral-900 px-4 py-2.5 text-sm text-white placeholder-neutral-600 outline-none transition-colors focus:border-neutral-500"
              style={{
                minHeight: "42px",
                maxHeight: "120px",
              }}
              onInput={(e) => {
                const target = e.target as HTMLTextAreaElement;
                target.style.height = "auto";
                target.style.height = `${Math.min(target.scrollHeight, 120)}px`;
              }}
            />
            <button
              onClick={sendMessage}
              disabled={loading || !input.trim()}
              className="rounded-xl bg-white px-4 py-2.5 text-sm font-medium text-black transition-all hover:bg-neutral-200 disabled:cursor-not-allowed disabled:opacity-30"
            >
              전송
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
