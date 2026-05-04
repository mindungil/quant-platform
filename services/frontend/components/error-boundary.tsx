"use client";

import { Component, ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="flex flex-col items-center justify-center rounded-2xl border border-[#2e2e2e] bg-[#111111] p-10 text-center">
          <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-red-500/10">
            <span className="text-lg text-red-400">!</span>
          </div>
          <p className="text-sm font-medium text-white">
            문제가 발생했습니다
          </p>
          <p className="mt-1 text-xs text-[#a1a1a1]">
            {this.state.error?.message || "알 수 없는 오류가 발생했습니다"}
          </p>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            className="btn-secondary mt-4"
          >
            다시 시도
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-[#2e2e2e] bg-[#111111] p-10 text-center">
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-[#1c1c21]">
        <span className="text-lg text-[#a1a1a1]">--</span>
      </div>
      <p className="text-sm font-medium text-white">{title}</p>
      <p className="mt-1 text-xs text-[#a1a1a1]">{description}</p>
      {action && (
        <button onClick={action.onClick} className="btn-primary mt-4">
          {action.label}
        </button>
      )}
    </div>
  );
}

export function LoadingSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="grid gap-4">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton h-24 rounded-2xl" />
      ))}
    </div>
  );
}
