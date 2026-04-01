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
      setMessage("Account created. You can now sign in.");
      setIsLogin(true);
    } catch (err: any) {
      setMessage(`Registration failed: ${err.message || "Unknown error"}`);
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
        setMessage("Login failed: Invalid credentials");
        return;
      }
      setToken(response.access_token);
      setMessage("Logged in. Redirecting…");
      window.setTimeout(() => {
        window.location.href = "/dashboard";
      }, 300);
    } catch (err: any) {
      setMessage(`Login failed: ${err.message || "Unknown error"}`);
    }
  }

  return (
    <PageTransition>
      <main className="flex min-h-[70vh] items-center justify-center">
        <div className="w-full max-w-md">
          <div className="mb-8 text-center">
            <motion.div
              className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-neutral-900"
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ type: "spring", stiffness: 260, damping: 20, delay: 0.1 }}
            >
              <span className="text-lg font-bold text-white">Q</span>
            </motion.div>
            <h1 className="text-2xl font-semibold text-neutral-900">Quant Platform</h1>
            <p className="mt-2 text-sm text-neutral-500">
              Autonomous trading command deck
            </p>
          </div>

          <div className="card">
            <h2 className="mb-6 text-lg font-semibold text-neutral-900">
              {isLogin ? "Sign in" : "Create account"}
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
                      <label className="mb-1.5 block text-sm font-medium text-neutral-700">Display Name</label>
                      <input
                        className="input-field"
                        value={displayName}
                        onChange={(e) => setDisplayName(e.target.value)}
                        placeholder="Your name"
                      />
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
              <div>
                <label className="mb-1.5 block text-sm font-medium text-neutral-700">Email</label>
                <input
                  className="input-field"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                />
              </div>
              <div>
                <label className="mb-1.5 block text-sm font-medium text-neutral-700">Password</label>
                <input
                  className="input-field"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter password"
                />
              </div>

              <button
                className="btn-primary w-full"
                onClick={isLogin ? login : register}
              >
                {isLogin ? "Sign in" : "Create account"}
              </button>
            </div>

            <div className="mt-6 text-center">
              <button
                className="text-sm text-neutral-500 hover:text-neutral-900"
                onClick={() => setIsLogin(!isLogin)}
              >
                {isLogin ? "Don't have an account? Register" : "Already have an account? Sign in"}
              </button>
            </div>

            {message && (
              <pre className="mt-4 rounded-lg border border-neutral-200 bg-neutral-50 p-3 text-xs text-neutral-600">
                {message}
              </pre>
            )}
          </div>
        </div>
      </main>
    </PageTransition>
  );
}
