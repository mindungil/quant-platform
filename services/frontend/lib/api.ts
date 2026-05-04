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

/**
 * Auth endpoints handle their own error responses (e.g. wrong password = 401).
 * Anywhere else, a 401 means the session is dead and we bounce to /login.
 */
function isAuthEndpoint(path: string) {
  return path.startsWith("/auth/");
}

export async function gatewayFetch(path: string, init?: RequestInit) {
  const token = getToken();
  const headers = new Headers(init?.headers ?? {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (!headers.has("Content-Type") && init?.body) headers.set("Content-Type", "application/json");
  const response = await fetch(`${gatewayBase}${path}`, { ...init, headers, cache: "no-store" });

  if (response.status === 401 && !isAuthEndpoint(path)) {
    // Session expired on a protected endpoint — clear and bounce to login.
    clearToken();
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
    throw new Error("인증이 만료되었습니다");
  }

  if (!response.ok) {
    // For auth endpoints, prefer the server's actual error detail so the
    // login form can show "wrong password" etc. instead of a generic message.
    if (isAuthEndpoint(path)) {
      let detail = "";
      try {
        const body = await response.json();
        detail = body?.detail || body?.message || body?.error || "";
      } catch { /* fall through */ }
      const fallback = response.status === 401 ? "이메일 또는 비밀번호가 올바르지 않습니다"
        : response.status === 409 ? "이미 가입된 이메일입니다"
        : response.status === 422 ? "입력값이 올바르지 않습니다"
        : "요청을 처리할 수 없습니다";
      throw new Error(detail || fallback);
    }

    const safeMessage = response.status === 403 ? "접근 권한이 없습니다"
      : response.status === 404 ? "요청한 리소스를 찾을 수 없습니다"
      : response.status === 429 ? "요청이 너무 많습니다. 잠시 후 다시 시도해주세요"
      : response.status >= 500 ? "서버 오류가 발생했습니다"
      : "요청 처리 중 오류가 발생했습니다";
    throw new Error(safeMessage);
  }
  try {
    return await response.json();
  } catch {
    return {};
  }
}
