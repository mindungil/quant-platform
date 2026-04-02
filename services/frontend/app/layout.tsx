import "./globals.css";
import type { ReactNode } from "react";
import { Navigation } from "../components/navigation";

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <head>
        <title>퀀트 플랫폼</title>
        <meta name="description" content="AI 에이전트 기반 자율 트레이딩 플랫폼" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <div className="min-h-screen">
          <header className="sticky top-0 z-50 border-b border-neutral-800 bg-neutral-900">
            <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-6">
              <div className="flex items-center gap-2.5">
                <div className="flex h-6 w-6 items-center justify-center rounded bg-white">
                  <span className="text-xs font-bold text-neutral-900">Q</span>
                </div>
                <span className="text-sm font-semibold tracking-tight text-white">
                  퀀트
                </span>
              </div>
              <Navigation />
            </div>
          </header>
          <main className="mx-auto max-w-7xl px-6 py-8">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
