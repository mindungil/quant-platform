"use client";

export const gatewayBase = process.env.NEXT_PUBLIC_GATEWAY_BASE_URL ?? "http://localhost:8017";

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
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}
