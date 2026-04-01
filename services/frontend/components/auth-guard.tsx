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
        <div className="panel animate-pulse">
          <p className="text-white/60">Authenticating...</p>
        </div>
      </main>
    );
  }

  if (!authorized) {
    return (
      <main className="grid gap-6">
        <div className="panel">
          <h2 className="text-2xl font-semibold">Session Expired</h2>
          <p className="mt-3 text-white/70">
            Redirecting to login...
          </p>
        </div>
      </main>
    );
  }

  return <>{children}</>;
}
