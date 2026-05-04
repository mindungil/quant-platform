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
      router.replace("/login");
      return;
    }

    const claims = readTokenClaims();
    if (!claims) {
      router.replace("/login");
      return;
    }

    if (claims.exp && claims.exp * 1000 < Date.now()) {
      router.replace("/login");
      return;
    }

    setAuthorized(true);
  }, [router]);

  // Show nothing while checking auth or redirecting — no flash of "unauthorized" content
  if (!authorized) {
    return null;
  }

  return <>{children}</>;
}
