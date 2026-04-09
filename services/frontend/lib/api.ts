"use client";

// All API calls go through /api/gateway/* on the same origin (Next.js rewrites to gateway)
// This works with VS Code port forwarding, reverse proxies, etc. — only port 8018 needed.
export const gatewayBase = "/api/gateway";

type TokenClaims = {
  sub?: string;
  email?: string;
  roles?: string[];
  exp?: number;
};

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem("quant_access_token");
}

export function setToken(token: string) {
  if (typeof window !== "undefined") {
    window.localStorage.setItem("quant_access_token", token);
  }
}

export function clearToken() {
  if (typeof window !== "undefined") {
    window.localStorage.removeItem("quant_access_token");
  }
}

export function readTokenClaims(): TokenClaims | null {
  const token = getToken();
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length < 2) return null;
  try {
    const payload = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/")));
    return payload as TokenClaims;
  } catch {
    return null;
  }
}

export function hasRole(role: string): boolean {
  const claims = readTokenClaims();
  return claims?.roles?.includes(role) ?? false;
}

export async function gatewayFetch(path: string, init?: RequestInit) {
  const token = getToken();
  const headers = new Headers(init?.headers ?? {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (!headers.has("Content-Type") && init?.body) headers.set("Content-Type", "application/json");
  const response = await fetch(`${gatewayBase}${path}`, { ...init, headers, cache: "no-store" });
  if (response.status === 401) {
    // Clear token and redirect to login
    clearToken();
    if (typeof window !== "undefined") {
      window.location.href = "/";
    }
    throw new Error("인증이 만료되었습니다");
  }
  if (!response.ok) {
    const errorText = await response.text();
    // Don't expose internal error details to client
    const safeMessage = response.status === 401 ? "인증이 필요합니다"
      : response.status === 403 ? "접근 권한이 없습니다"
      : response.status === 404 ? "요청한 리소스를 찾을 수 없습니다"
      : response.status === 429 ? "요청이 너무 많습니다. 잠시 후 다시 시도해주세요"
      : response.status >= 500 ? "서버 오류가 발생했습니다"
      : "요청 처리 중 오류가 발생했습니다";
    throw new Error(safeMessage);
  }
  try {
    return response.json();
  } catch {
    return {};
  }
}
