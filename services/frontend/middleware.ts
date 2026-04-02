import { NextRequest, NextResponse } from "next/server";

/**
 * Proxy /api/gateway/* requests to the backend gateway service.
 * This allows the frontend to work with only port 8018 forwarded
 * (VS Code port forwarding, reverse proxy, etc.)
 */
export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (pathname.startsWith("/api/gateway/")) {
    const gatewayUrl =
      process.env.API_GATEWAY_BASE_URL || "http://localhost:8017";
    const targetPath = pathname.replace("/api/gateway", "");
    const target = `${gatewayUrl}${targetPath}${request.nextUrl.search}`;

    const headers = new Headers(request.headers);
    // Remove host header to avoid conflicts
    headers.delete("host");

    const resp = await fetch(target, {
      method: request.method,
      headers,
      body: request.method !== "GET" && request.method !== "HEAD"
        ? await request.text()
        : undefined,
    });

    const responseHeaders = new Headers(resp.headers);
    // Ensure CORS headers for the proxied response
    responseHeaders.set("Access-Control-Allow-Origin", "*");

    return new NextResponse(resp.body, {
      status: resp.status,
      statusText: resp.statusText,
      headers: responseHeaders,
    });
  }

  return NextResponse.next();
}

export const config = {
  matcher: "/api/gateway/:path*",
};
