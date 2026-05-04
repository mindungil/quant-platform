"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { gatewayFetch, setToken, getToken, readTokenClaims } from "../../lib/api";
import { motion, AnimatePresence } from "framer-motion";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [message, setMessage] = useState("");
  const [messageKind, setMessageKind] = useState<"err" | "ok" | "">("");
  const [isLogin, setIsLogin] = useState(true);
  const [loading, setLoading] = useState(false);
  const [agreeTerms, setAgreeTerms] = useState(false);

  // Already logged in → redirect to dashboard immediately
  useEffect(() => {
    const token = getToken();
    if (token) {
      const claims = readTokenClaims();
      if (claims && claims.exp && claims.exp * 1000 > Date.now()) {
        router.replace("/dashboard");
      }
    }
  }, [router]);

  async function register() {
    if (!agreeTerms) {
      setMessage("이용약관에 동의해주세요");
      setMessageKind("err");
      return;
    }
    // Pre-validate password against backend rules so users get instant feedback
    if (password.length < 8 || !/[A-Z]/.test(password) || !/\d/.test(password)) {
      setMessage("비밀번호 규칙: 8자 이상 · 대문자 1개 이상 · 숫자 1개 이상");
      setMessageKind("err");
      return;
    }
    if (!displayName.trim()) {
      setMessage("이름을 입력해주세요");
      setMessageKind("err");
      return;
    }
    try {
      setMessage("");
      setMessageKind("");
      setLoading(true);
      await gatewayFetch("/auth/register", {
        method: "POST",
        body: JSON.stringify({ email, password, display_name: displayName, plan: "premium" }),
      });
      setMessage("계정이 생성되었습니다 — 로그인해주세요");
      setMessageKind("ok");
      setIsLogin(true);
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : "알 수 없는 오류");
      setMessageKind("err");
    } finally {
      setLoading(false);
    }
  }

  async function login() {
    try {
      setMessage("");
      setMessageKind("");
      setLoading(true);
      const response = await gatewayFetch("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      if (!response?.access_token) {
        setMessage("이메일 또는 비밀번호가 올바르지 않습니다");
        setMessageKind("err");
        return;
      }
      // Token write to localStorage is synchronous — no need to delay the redirect.
      setToken(response.access_token);
      // Use replace so back-button doesn't return to /login.
      router.replace("/dashboard");
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : "알 수 없는 오류");
      setMessageKind("err");
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (loading) return;
    if (!email || !password || (!isLogin && !agreeTerms)) return;
    isLogin ? login() : register();
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-ink overflow-hidden">
      {/* Cool ambient lights */}
      <div className="pointer-events-none fixed inset-0">
        <div className="bg-orb-light max-w-full" style={{ top: "-120px", left: "-100px", width: "min(620px, 95vw)", height: "min(620px, 95vw)" }} />
        <div className="bg-orb-dim max-w-full" style={{ bottom: "-80px", right: "-60px", width: "min(420px, 80vw)", height: "min(420px, 80vw)" }} />
        {/* Grid lines — extremely faint terminal background */}
        <div className="absolute inset-0 opacity-[0.07]" style={{
          backgroundImage: "linear-gradient(rgba(251,189,46,0.4) 1px, transparent 1px), linear-gradient(90deg, rgba(251,189,46,0.4) 1px, transparent 1px)",
          backgroundSize: "64px 64px",
        }} />
      </div>

      <motion.div
        className="relative z-10 w-full max-w-[420px] px-5 sm:px-6"
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.55, ease: [0.22, 1, 0.36, 1] }}
      >
        {/* ── Header ── */}
        <div className="mb-7 text-center">
          <div className="flex items-center justify-center gap-2 mb-4">
            <span className="font-mono text-[12px] tracking-[0.25em] text-amber">[</span>
            <span className="font-mono text-[15px] font-bold tracking-[0.22em] text-paper uppercase">quant</span>
            <span className="font-mono text-[12px] tracking-[0.25em] text-amber">]</span>
            <span className="amber-led ml-1" aria-hidden />
          </div>
          <p className="label-eyebrow-amber">QUANT TRADING TERMINAL</p>
          <p className="mt-2 label-eyebrow text-paper-low">v4.5 // half_kelly</p>
        </div>

        {/* ── Form panel ── */}
        <motion.form
          onSubmit={handleSubmit}
          className="relative bg-ink-50 border border-rule-loud p-6 sm:p-8 panel-amber-tab"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15, duration: 0.5 }}
        >
          <div className="flex items-baseline justify-between mb-5">
            <p className="label-eyebrow-amber">
              {isLogin ? "AUTH.LOGIN" : "AUTH.REGISTER"}
            </p>
            <p className="label-eyebrow tabular">
              {isLogin ? "01/01" : "01/02"}
            </p>
          </div>

          <div className="space-y-4">
            <AnimatePresence>
              {!isLogin && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  style={{ overflow: "hidden" }}
                >
                  <div className="pb-4">
                    <label className="label-eyebrow block mb-2">DISPLAY_NAME</label>
                    <input
                      className="input-field"
                      value={displayName}
                      onChange={(e) => setDisplayName(e.target.value)}
                      placeholder="trader_handle"
                    />
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            <div>
              <label className="label-eyebrow block mb-2">EMAIL</label>
              <input
                className="input-field"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="user@domain.com"
                autoComplete="email"
                required
              />
            </div>

            <div>
              <label className="label-eyebrow block mb-2">PASSWORD</label>
              <input
                className="input-field"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="•••••••••"
                autoComplete={isLogin ? "current-password" : "new-password"}
                required
              />
              {!isLogin && (
                <ul className="mt-2.5 space-y-1 font-mono text-[10px] tracking-[0.06em] uppercase">
                  {[
                    { ok: password.length >= 8,        label: "8자 이상" },
                    { ok: /[A-Z]/.test(password),       label: "대문자 1개 이상" },
                    { ok: /\d/.test(password),          label: "숫자 1개 이상" },
                  ].map((rule) => (
                    <li key={rule.label} className="flex items-baseline gap-2">
                      <span className={`inline-block w-2 text-center ${rule.ok ? "text-mint" : "text-paper-low"}`}>
                        {rule.ok ? "✓" : "·"}
                      </span>
                      <span className={rule.ok ? "text-paper-dim" : "text-paper-mute"}>{rule.label}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {!isLogin && (
              <label htmlFor="agree-terms" className="flex items-start gap-2.5 cursor-pointer pt-1">
                <input
                  type="checkbox"
                  id="agree-terms"
                  checked={agreeTerms}
                  onChange={(e) => setAgreeTerms(e.target.checked)}
                  className="mt-0.5 h-4 w-4 cursor-pointer accent-amber"
                />
                <span className="font-prose text-xs text-paper-dim leading-relaxed">
                  <a href="/terms" target="_blank" className="text-amber hover:underline">이용약관</a>에 동의합니다 — 투자 위험 고지 포함.
                </span>
              </label>
            )}

            <motion.button
              type="submit"
              className="btn-primary w-full"
              disabled={loading || !email || !password || (!isLogin && !agreeTerms)}
              whileTap={{ scale: 0.985 }}
            >
              {loading ? (
                <motion.span
                  className="inline-block h-3.5 w-3.5 rounded-full border-2 border-current border-t-transparent"
                  animate={{ rotate: 360 }}
                  transition={{ duration: 0.7, repeat: Infinity, ease: "linear" }}
                />
              ) : isLogin ? (
                "→ AUTHENTICATE"
              ) : (
                "→ CREATE ACCOUNT"
              )}
            </motion.button>
          </div>

          {/* ── Mode toggle ── */}
          <div className="mt-6 pt-5 border-t border-rule text-center">
            <button
              type="button"
              className="font-mono text-[11px] uppercase tracking-[0.14em] text-paper-mute transition-colors hover:text-amber"
              onClick={() => {
                setIsLogin(!isLogin);
                setMessage("");
                setMessageKind("");
              }}
            >
              {isLogin ? "─ NO ACCOUNT? REGISTER →" : "─ HAVE ACCOUNT? SIGN IN →"}
            </button>
          </div>

          {/* ── Status line ── */}
          <AnimatePresence>
            {message && (
              <motion.div
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                className={`mt-5 flex items-baseline gap-2.5 px-3 py-2.5 border ${
                  messageKind === "err"
                    ? "border-coral/30 bg-coral/5 text-coral"
                    : "border-mint/30 bg-mint/5 text-mint"
                }`}
              >
                <span className={`amber-led-static ${messageKind === "err" ? "led-coral" : "led-mint"}`} aria-hidden />
                <span className="font-mono text-[11px] uppercase tracking-[0.08em] text-paper-low shrink-0">
                  {messageKind === "err" ? "ERR" : "OK"}
                </span>
                <span className="font-prose text-[13px]">{message}</span>
              </motion.div>
            )}
          </AnimatePresence>
        </motion.form>

        {/* ── Footer ── */}
        <p className="mt-6 text-center label-eyebrow text-paper-low">
          ENCRYPTED // JWT // TLS 1.3
        </p>
      </motion.div>
    </div>
  );
}
