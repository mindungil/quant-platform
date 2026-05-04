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
    return (
      <main className="rounded border border-neutral-200 bg-white p-6">
        <p className="text-[#a1a1a1]">관리자 권한 확인 중...</p>
      </main>
    );
  }

  if (!authorized) {
    return (
      <main className="rounded border border-neutral-200 bg-white p-6">
        <h2 className="text-2xl font-semibold text-neutral-900">403</h2>
        <p className="mt-3 text-[#a1a1a1]">이 페이지는 관리자 권한이 필요합니다. 대시보드로 이동합니다.</p>
      </main>
    );
  }

  return <>{children}</>;
}
