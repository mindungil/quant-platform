import "./globals.css";
import Link from "next/link";
import type { ReactNode } from "react";

const links = [
  ["/dashboard", "Dashboard"],
  ["/signals", "Signals"],
  ["/feed", "Feed"],
  ["/strategies", "Strategies"],
  ["/settings", "Settings"]
];

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
            <nav className="flex flex-wrap gap-3 text-sm">
              {links.map(([href, label]) => (
                <Link key={href} href={href} className="rounded-full border border-white/10 px-4 py-2 hover:bg-white/10">
                  {label}
                </Link>
              ))}
            </nav>
          </header>
          {children}
        </div>
      </body>
    </html>
  );
}
