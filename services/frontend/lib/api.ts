"use client";

export const gatewayBase = process.env.NEXT_PUBLIC_GATEWAY_BASE_URL ?? "http://localhost:8017";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem("quant_access_token");
}

export function setToken(token: string) {
  if (typeof window !== "undefined") {
    window.localStorage.setItem("quant_access_token", token);
  }
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
