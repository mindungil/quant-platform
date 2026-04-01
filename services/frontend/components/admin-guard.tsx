"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { hasRole } from "../lib/api";

export function AdminGuard({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [authorized, setAuthorized] = useState<boolean | null>(null);

  useEffect(() => {
    const allowed = hasRole("admin");
    setAuthorized(allowed);
    if (!allowed) {
      window.setTimeout(() => router.replace("/dashboard"), 800);
    }
  }, [router]);

  if (authorized === null) {
    return <main className="panel">Checking admin access…</main>;
  }

  if (!authorized) {
    return (
      <main className="panel">
        <h2 className="text-2xl font-semibold">403</h2>
        <p className="mt-3 text-white/70">Admin access is required for this section. Redirecting to the dashboard.</p>
      </main>
    );
  }

  return <>{children}</>;
}
