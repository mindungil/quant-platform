import "./globals.css";
import type { ReactNode } from "react";
import { Navigation } from "../components/navigation";

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="mx-auto min-h-screen w-full max-w-7xl px-4 py-8">
          <header className="mb-6 flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div>
              <p className="text-sm uppercase tracking-[0.3em] text-mint">Quant Platform</p>
              <h1 className="text-4xl font-semibold tracking-tight">Production Dashboard</h1>
            </div>
            <Navigation />
          </header>
          {children}
        </div>
      </body>
    </html>
  );
}
