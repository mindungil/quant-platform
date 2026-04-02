"use client";

import { useState } from "react";
import { gatewayFetch, setToken } from "../lib/api";
import { PageTransition, motion, AnimatePresence } from "../components/motion";

export default function HomePage() {
  const [email, setEmail] = useState("demo@example.com");
  const [password, setPassword] = useState("password123");
  const [displayName, setDisplayName] = useState("Demo");
  const [message, setMessage] = useState("");
  const [isLogin, setIsLogin] = useState(true);

  async function register() {
    try {
      setMessage("");
      const response = await gatewayFetch("/auth/register", {
        method: "POST",
        body: JSON.stringify({ email, password, display_name: displayName, plan: "premium" })
      });
      setMessage("계정이 생성되었습니다. 로그인해주세요.");
      setIsLogin(true);
    } catch (err: any) {
      setMessage(`회원가입 실패: ${err.message || "Unknown error"}`);
    }
  }

  async function login() {
    try {
      setMessage("");
      const response = await gatewayFetch("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password })
      });
      if (!response.access_token) {
        setMessage("로그인 실패: 잘못된 인증 정보");
        return;
      }
      setToken(response.access_token);
      setMessage("로그인 성공. 이동 중…");
      window.setTimeout(() => {
        window.location.href = "/dashboard";
      }, 300);
    } catch (err: any) {
      setMessage(`로그인 실패: ${err.message || "Unknown error"}`);
    }
  }

  return (
    <PageTransition>
      <main className="flex min-h-[70vh] items-center justify-center">
        <div className="w-full max-w-md">
          <div className="mb-8 text-center">
            <motion.div
              className="mx-auto mb-4 flex h-10 w-10 items-center justify-center rounded bg-neutral-900"
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ type: "spring", stiffness: 260, damping: 20, delay: 0.1 }}
            >
              <span className="font-mono text-sm font-bold text-white">Q</span>
            </motion.div>
            <h1 className="text-2xl font-semibold text-neutral-900">퀀트 플랫폼</h1>
            <p className="mt-2 text-sm text-neutral-400">
              자율 트레이딩 커맨드 덱
            </p>
          </div>

          <div className="rounded border border-neutral-200 bg-white p-6">
            <h2 className="mb-6 text-lg font-semibold text-neutral-900">
              {isLogin ? "로그인" : "회원가입"}
            </h2>

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
                      <label className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-neutral-400">표시 이름</label>
                      <input
                        className="input-field"
                        value={displayName}
                        onChange={(e) => setDisplayName(e.target.value)}
                        placeholder="이름 입력"
                      />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
              <div>
                <label className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-neutral-400">이메일</label>
                <input
                  className="input-field"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="email@example.com"
                />
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-neutral-400">비밀번호</label>
                <input
                  className="input-field"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="비밀번호 입력"
                />
              </div>

              <button
                className="btn-primary w-full"
                onClick={isLogin ? login : register}
              >
                {isLogin ? "로그인" : "회원가입"}
              </button>
            </div>

            <div className="mt-6 text-center">
              <button
                className="text-sm text-neutral-400 hover:text-neutral-900 transition"
                onClick={() => setIsLogin(!isLogin)}
              >
                {isLogin ? "계정이 없으신가요? 회원가입" : "이미 계정이 있으신가요? 로그인"}
              </button>
            </div>

            {message && (
              <pre className="mt-4 rounded border border-neutral-200 bg-white p-3 font-mono text-xs text-neutral-600">
                {message}
              </pre>
            )}
          </div>
        </div>
      </main>
    </PageTransition>
  );
}
