"use client";

import { useCallback, useEffect, useState } from "react";

import { AdminGuard } from "../../../components/admin-guard";
import { gatewayFetch } from "../../../lib/api";
import {
  PageTransition,
  StaggerContainer,
  StaggerItem,
} from "../../../components/motion";

type EvaluationPosture = {
  running: boolean;
  active_cycle_id?: string | null;
  completed_cycles: number;
  available_cycles: string[];
  latest_cycle_id?: string | null;
  latest_phase?: string | null;
  latest_verdict?: string | null;
  latest_blended_score?: { value?: number } | null;
};

type SummaryBlock = Record<string, unknown>;

type FailureRecord = {
  category?: string;
  severity?: string;
  symptom?: string;
  impact?: string;
};

export default function AdminEvaluationPage() {
  const [posture, setPosture] = useState<EvaluationPosture | null>(null);
  const [realtime, setRealtime] = useState<SummaryBlock | null>(null);
  const [historical, setHistorical] = useState<SummaryBlock | null>(null);
  const [scorecard, setScorecard] = useState<SummaryBlock | null>(null);
  const [failures, setFailures] = useState<FailureRecord[]>([]);

  const load = useCallback(async () => {
    const [postureResp, realtimeResp, historicalResp, scoreResp, failuresResp] = await Promise.allSettled([
      gatewayFetch("/admin/evaluation/posture"),
      gatewayFetch("/admin/evaluation/realtime-summary"),
      gatewayFetch("/admin/evaluation/historical-summary"),
      gatewayFetch("/admin/evaluation/blended-score"),
      gatewayFetch("/admin/evaluation/failures"),
    ]);
    setPosture(postureResp.status === "fulfilled" ? postureResp.value : null);
    setRealtime(realtimeResp.status === "fulfilled" ? realtimeResp.value : null);
    setHistorical(historicalResp.status === "fulfilled" ? historicalResp.value : null);
    setScorecard(scoreResp.status === "fulfilled" ? scoreResp.value : null);
    setFailures(
      failuresResp.status === "fulfilled" ? (failuresResp.value.items ?? []) : [],
    );
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <AdminGuard>
      <PageTransition>
        <main className="grid gap-6">
          <section className="rounded border border-[#2e2e2e] bg-[#111111] p-6">
            <p className="text-sm font-medium uppercase tracking-wider text-[#a1a1a1]">EVALUATION LOOP</p>
            <h2 className="mt-1 text-2xl font-semibold text-white">7-Cycle Shadow Evaluation</h2>
            <p className="mt-2 text-sm text-[#a1a1a1]">
              24시간 shadow 운영과 과거 재현 점수를 함께 보는 운영자 전용 평가 표면
            </p>
          </section>

          <StaggerContainer className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StaggerItem>
              <div className="rounded border border-[#2e2e2e] bg-[#111111] p-5">
                <p className="text-xs uppercase tracking-wider text-[#a1a1a1]">현재 사이클</p>
                <p className="mt-2 font-mono text-2xl font-semibold text-white">{posture?.active_cycle_id ?? "--"}</p>
              </div>
            </StaggerItem>
            <StaggerItem>
              <div className="rounded border border-[#2e2e2e] bg-[#111111] p-5">
                <p className="text-xs uppercase tracking-wider text-[#a1a1a1]">완료 회차</p>
                <p className="mt-2 font-mono text-2xl font-semibold text-white">{posture?.completed_cycles ?? 0}</p>
              </div>
            </StaggerItem>
            <StaggerItem>
              <div className="rounded border border-[#2e2e2e] bg-[#111111] p-5">
                <p className="text-xs uppercase tracking-wider text-[#a1a1a1]">최신 Verdict</p>
                <p className="mt-2 font-mono text-2xl font-semibold text-white">{posture?.latest_verdict ?? "--"}</p>
              </div>
            </StaggerItem>
            <StaggerItem>
              <div className="rounded border border-[#2e2e2e] bg-[#111111] p-5">
                <p className="text-xs uppercase tracking-wider text-[#a1a1a1]">Blended Score</p>
                <p className="mt-2 font-mono text-2xl font-semibold text-white">
                  {posture?.latest_blended_score?.value ?? "--"}
                </p>
              </div>
            </StaggerItem>
          </StaggerContainer>

          <section className="grid gap-4 lg:grid-cols-2">
            <div className="rounded border border-[#2e2e2e] bg-[#111111] p-6">
              <p className="text-sm font-medium uppercase tracking-wider text-[#a1a1a1]">REALTIME SHADOW</p>
              <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-xs text-[#d4d4d4]">
                {JSON.stringify(realtime ?? {}, null, 2)}
              </pre>
            </div>
            <div className="rounded border border-[#2e2e2e] bg-[#111111] p-6">
              <p className="text-sm font-medium uppercase tracking-wider text-[#a1a1a1]">HISTORICAL REPLAY</p>
              <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-xs text-[#d4d4d4]">
                {JSON.stringify(historical ?? {}, null, 2)}
              </pre>
            </div>
          </section>

          <section className="rounded border border-[#2e2e2e] bg-[#111111] p-6">
            <p className="text-sm font-medium uppercase tracking-wider text-[#a1a1a1]">SCORECARD</p>
            <pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-xs text-[#d4d4d4]">
              {JSON.stringify(scorecard ?? {}, null, 2)}
            </pre>
          </section>

          <section className="rounded border border-[#2e2e2e] bg-[#111111] p-6">
            <p className="text-sm font-medium uppercase tracking-wider text-[#a1a1a1]">FAILURES</p>
            {failures.length === 0 ? (
              <p className="mt-3 text-sm text-[#a1a1a1]">최근 실패 기록 없음</p>
            ) : (
              <StaggerContainer className="mt-4 space-y-2">
                {failures.map((failure, index) => (
                  <StaggerItem key={`${failure.symptom ?? "failure"}-${index}`}>
                    <div className="rounded border border-[#2e2e2e] bg-[#111111] px-4 py-3">
                      <div className="flex items-center gap-3">
                        <span className="inline-flex rounded-full bg-[#1c1c21] px-2 py-0.5 text-xs font-medium text-[#a1a1a1]">
                          {failure.category ?? "unknown"}
                        </span>
                        <span className="font-mono text-xs text-white">{failure.severity ?? "?"}</span>
                      </div>
                      <p className="mt-2 text-sm font-medium text-white">{failure.symptom ?? "unknown failure"}</p>
                      <p className="mt-1 text-sm text-[#a1a1a1]">{failure.impact ?? ""}</p>
                    </div>
                  </StaggerItem>
                ))}
              </StaggerContainer>
            )}
          </section>
        </main>
      </PageTransition>
    </AdminGuard>
  );
}
