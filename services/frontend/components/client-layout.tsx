"use client";
import { ReactNode } from "react";
import { ToastProvider } from "./toast";
import { SplashScreen } from "./splash-screen";
import { OnboardingTour } from "./onboarding-tour";

export function ClientLayout({ children }: { children: ReactNode }) {
  return (
    <ToastProvider>
      <SplashScreen>{children}</SplashScreen>
      <OnboardingTour />
    </ToastProvider>
  );
}
