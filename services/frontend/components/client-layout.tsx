"use client";
import { ReactNode } from "react";
import { ToastProvider } from "./toast";
import { SplashScreen } from "./splash-screen";

export function ClientLayout({ children }: { children: ReactNode }) {
  return (
    <ToastProvider>
      <SplashScreen>{children}</SplashScreen>
    </ToastProvider>
  );
}
