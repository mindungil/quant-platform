import "./globals.css";
import type { ReactNode } from "react";
import { Navigation } from "../components/navigation";
import { ClientLayout } from "../components/client-layout";

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko" className="dark">
      <head>
        <title>Quant — AI Trading</title>
        <meta name="description" content="AI 자동 매매 플랫폼" />
        <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
        <link rel="manifest" href="/manifest.json" />
        <meta name="theme-color" content="#09090b" />
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
        <meta name="apple-mobile-web-app-title" content="Quant" />
        <link rel="apple-touch-icon" href="/icon-192.png" />
        <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="bg-zinc-950 text-neutral-200">
        <ClientLayout>
          <div className="min-h-screen">
            <header className="sticky top-0 z-50 border-b border-white/[0.06] bg-zinc-950/90 backdrop-blur-xl">
              <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6 gap-3">
                <a href="/dashboard" className="flex h-7 w-7 items-center justify-center rounded-lg bg-white transition-transform hover:scale-105">
                  <span className="text-xs font-bold text-black">Q</span>
                </a>
                <Navigation />
              </div>
            </header>
            <main className="mx-auto max-w-7xl px-4 sm:px-6 py-6 sm:py-8">
              {children}
            </main>
          </div>
        </ClientLayout>
      </body>
    </html>
  );
}
