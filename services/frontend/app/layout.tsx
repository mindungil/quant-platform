import "./globals.css";
import type { ReactNode } from "react";
import { Navigation } from "../components/navigation";

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <head>
        <title>퀀트 플랫폼</title>
        <meta name="description" content="자율 트레이딩 커맨드 덱 — 실시간 시그널, 포트폴리오 관리, 전략 실행" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <div className="mx-auto min-h-screen w-full max-w-7xl px-6 py-8">
          <header className="mb-8 flex items-center justify-between border-b border-neutral-200 pb-6">
            <div className="flex items-center gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-neutral-900">
                <span className="text-sm font-bold text-white">Q</span>
              </div>
              <span className="text-lg font-semibold tracking-tight text-neutral-900">
                퀀트 플랫폼
              </span>
            </div>
            <Navigation />
          </header>
          {children}
        </div>
      </body>
    </html>
  );
}
