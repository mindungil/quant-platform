"use client";

import { useState } from "react";
import { gatewayFetch, setToken } from "../lib/api";
import { motion, AnimatePresence } from "framer-motion";

export default function HomePage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [message, setMessage] = useState("");
  const [isLogin, setIsLogin] = useState(true);
  const [loading, setLoading] = useState(false);

  async function register() {
    try {
      setMessage("");
      setLoading(true);
      await gatewayFetch("/auth/register", {
        method: "POST",
        body: JSON.stringify({ email, password, display_name: displayName, plan: "premium" }),
      });
      setMessage("계정이 생성되었습니다. 로그인해주세요.");
      setIsLogin(true);
    } catch (err: unknown) {
      setMessage(`회원가입 실패: ${err instanceof Error ? err.message : "알 수 없는 오류"}`);
    } finally {
      setLoading(false);
    }
  }

  async function login() {
    try {
      setMessage("");
      setLoading(true);
      const response = await gatewayFetch("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      if (!response.access_token) {
        setMessage("이메일 또는 비밀번호를 확인해주세요.");
        return;
      }
      setToken(response.access_token);
      setMessage("로그인 성공");
      window.setTimeout(() => {
        window.location.href = "/dashboard";
      }, 300);
    } catch (err: unknown) {
      setMessage(`로그인 실패: ${err instanceof Error ? err.message : "알 수 없는 오류"}`);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950">
      {/* Background glow */}
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="bg-orb-light" style={{ top: "-100px", left: "-80px", width: "500px", height: "500px" }} />
        <div className="bg-orb-dim" style={{ bottom: "-80px", right: "-60px", width: "400px", height: "400px" }} />
        <div className="bg-orb-light" style={{ top: "40%", right: "10%", width: "250px", height: "250px", opacity: 0.5 }} />
      </div>

      <motion.div
        className="relative z-10 w-full max-w-sm px-6"
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
      >
        {/* Logo */}
        <motion.div
          className="mb-10 text-center"
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.15, type: "spring", stiffness: 200, damping: 20 }}
        >
          <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-xl bg-white shadow-[0_0_20px_rgba(255,255,255,0.15)]">
            <span className="text-lg font-bold text-black">Q</span>
          </div>
          <h1 className="text-xl font-bold tracking-tight text-white">
            {isLogin ? "로그인" : "계정 만들기"}
          </h1>
          <p className="mt-2 text-sm text-neutral-400 leading-relaxed">
            투자를 모르셔도 괜찮아요.
            <br />
            AI가 대신 분석해드릴게요.
          </p>
          <p className="mt-3 text-[11px] text-neutral-500">
            24시간 자동 분석 {"\u00B7"} 2,000+ 분석 완료 {"\u00B7"} 초보자 맞춤 설명
          </p>
        </motion.div>

        {/* Form */}
        <motion.div
          className="rounded-2xl border-glow bg-white/[0.04] p-6 backdrop-blur-md"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.25, duration: 0.5 }}
        >
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
                    <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-widest text-neutral-500">
                      이름
                    </label>
                    <input
                      className="w-full rounded-lg border border-neutral-700 bg-neutral-800/50 px-3.5 py-2.5 text-sm text-white placeholder-neutral-600 outline-none transition-colors focus:border-white/[0.30]"
                      value={displayName}
                      onChange={(e) => setDisplayName(e.target.value)}
                      placeholder="표시 이름"
                    />
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            <div>
              <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-widest text-neutral-500">
                이메일
              </label>
              <input
                className="w-full rounded-lg border border-neutral-700 bg-neutral-800/50 px-3.5 py-2.5 text-sm text-white placeholder-neutral-600 outline-none transition-colors focus:border-white/[0.30]"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="email@example.com"
              />
            </div>

            <div>
              <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-widest text-neutral-500">
                비밀번호
              </label>
              <input
                className="w-full rounded-lg border border-neutral-700 bg-neutral-800/50 px-3.5 py-2.5 text-sm text-white placeholder-neutral-600 outline-none transition-colors focus:border-white/[0.30]"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="8자 이상"
              />
            </div>

            <motion.button
              className="w-full rounded-lg bg-white py-2.5 text-sm font-semibold text-black disabled:opacity-40 hover:shadow-[0_0_20px_rgba(255,255,255,0.15)]"
              onClick={isLogin ? login : register}
              disabled={loading || !email || !password}
              whileTap={{ scale: 0.98 }}
            >
              {loading ? (
                <motion.span
                  className="inline-block h-4 w-4 rounded-full border-2 border-neutral-400 border-t-transparent"
                  animate={{ rotate: 360 }}
                  transition={{ duration: 0.7, repeat: Infinity, ease: "linear" }}
                />
              ) : isLogin ? (
                "로그인"
              ) : (
                "회원가입"
              )}
            </motion.button>
          </div>

          <div className="mt-5 text-center">
            <button
              className="text-sm text-neutral-500 transition-colors hover:text-white"
              onClick={() => {
                setIsLogin(!isLogin);
                setMessage("");
              }}
            >
              {isLogin ? "계정이 없으신가요?" : "이미 계정이 있으신가요?"}
            </button>
          </div>

          <AnimatePresence>
            {message && (
              <motion.p
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                className={`mt-4 rounded-lg px-3 py-2 text-center text-xs ${
                  message.includes("실패") || message.includes("확인")
                    ? "bg-red-500/10 text-red-400"
                    : "bg-emerald-500/10 text-emerald-400"
                }`}
              >
                {message}
              </motion.p>
            )}
          </AnimatePresence>
        </motion.div>
      </motion.div>
    </div>
  );
}
