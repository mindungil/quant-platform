import "./globals.css";
import type { ReactNode } from "react";
import { Navigation } from "../components/navigation";

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <title>Quant Platform</title>
        <meta name="description" content="Autonomous trading command deck — real-time signals, portfolio management, and strategy execution." />
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
                Quant Platform
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
