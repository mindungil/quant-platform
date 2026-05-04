import "./globals.css";
import type { ReactNode } from "react";
import { ClientLayout } from "../components/client-layout";
import { AppShell } from "../components/app-shell";

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko" className="dark">
      <head>
        <title>QUANT — TERMINAL</title>
        <meta name="description" content="Algorithmic trading terminal." />
        <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
        <link rel="manifest" href="/manifest.json" />
        <meta name="theme-color" content="#0a0c10" />
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
        <meta name="apple-mobile-web-app-title" content="Quant" />
        <link rel="apple-touch-icon" href="/favicon.svg" />
        <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
      </head>
      <body className="bg-ink text-paper">
        <ClientLayout>
          <AppShell>{children}</AppShell>
        </ClientLayout>
      </body>
    </html>
  );
}
