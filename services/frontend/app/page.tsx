"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getToken, readTokenClaims } from "../lib/api";

export default function HomePage() {
  const router = useRouter();

  useEffect(() => {
    const token = getToken();
    if (token) {
      const claims = readTokenClaims();
      if (claims && claims.exp && claims.exp * 1000 > Date.now()) {
        router.replace("/dashboard");
        return;
      }
    }
    router.replace("/intro");
  }, [router]);

  return null;
}
