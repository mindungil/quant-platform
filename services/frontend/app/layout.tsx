import "./globals.css";
import type { ReactNode } from "react";
import { Navigation } from "../components/navigation";
import { ClientLayout } from "../components/client-layout";

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko" className="dark">
      <head>
        <title>Quant</title>
        <meta name="description" content="AI 자율 트레이딩 플랫폼" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="bg-zinc-950 text-neutral-200">
        <ClientLayout>
          <div className="min-h-screen">
            <header className="sticky top-0 z-50 border-b border-white/[0.06] bg-zinc-950/90 backdrop-blur-xl">
              <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-6">
                <a href="/dashboard" className="flex h-7 w-7 items-center justify-center rounded-lg bg-white transition-transform hover:scale-105">
                  <span className="text-xs font-bold text-black">Q</span>
                </a>
                <Navigation />
              </div>
            </header>
            <main className="mx-auto max-w-7xl px-6 py-8">
              {children}
            </main>
          </div>
        </ClientLayout>
      </body>
    </html>
  );
}
