"use client";
import { ReactNode } from "react";
import { ToastProvider } from "./toast";

export function ClientLayout({ children }: { children: ReactNode }) {
  return <ToastProvider>{children}</ToastProvider>;
}
