"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { getToken, readTokenClaims } from "../lib/api";

export function AuthGuard({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [authorized, setAuthorized] = useState<boolean | null>(null);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setAuthorized(false);
      router.replace("/");
      return;
    }

    const claims = readTokenClaims();
    if (!claims) {
      setAuthorized(false);
      router.replace("/");
      return;
    }

    // Check expiration (exp is in seconds)
    if (claims.exp && claims.exp * 1000 < Date.now()) {
      setAuthorized(false);
      router.replace("/");
      return;
    }

    setAuthorized(true);
  }, [router]);

  if (authorized === null) {
    return (
      <main className="grid gap-6">
        <div className="rounded border border-neutral-200 bg-white p-6 animate-pulse">
          <p className="text-neutral-400">인증 중...</p>
        </div>
      </main>
    );
  }

  if (!authorized) {
    return (
      <main className="grid gap-6">
        <div className="rounded border border-neutral-200 bg-white p-6">
          <h2 className="text-2xl font-semibold text-neutral-900">세션 만료</h2>
          <p className="mt-3 text-neutral-500">
            로그인 페이지로 이동 중...
          </p>
        </div>
      </main>
    );
  }

  return <>{children}</>;
}
