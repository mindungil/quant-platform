import { NextResponse } from "next/server";
import { readFile } from "node:fs/promises";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const SNAPSHOTS_PATH = "/data/loop/snapshots.jsonl";
const STATE_PATH = "/data/loop/state.json";

interface Snapshot {
  iter: number;
  ts: string;
  paper: number;
  virtual: number;
  daily_ret_diff_bps: number;
  btc_30d_sr: number;
  eth_6m_sr: number;
  bnb_6m_sr: number;
  max_dd: number;
  warn_symbols: string[];
  n_trades: number;
}

export async function GET() {
  try {
    const [raw, stateRaw] = await Promise.all([
      readFile(SNAPSHOTS_PATH, "utf8"),
      readFile(STATE_PATH, "utf8"),
    ]);
    const snapshots: Snapshot[] = raw
      .split("\n")
      .filter((l) => l.trim().length > 0)
      .map((l) => JSON.parse(l));
    const state = JSON.parse(stateRaw);
    return NextResponse.json({ snapshots, state });
  } catch (err) {
    return NextResponse.json(
      {
        error: err instanceof Error ? err.message : "read failed",
        snapshots: [],
        state: null,
      },
      { status: 500 }
    );
  }
}
