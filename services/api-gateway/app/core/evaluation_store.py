from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class EvaluationStore:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)

    def _cycle_path(self, cycle_id: str) -> Path:
        return self.base_dir / cycle_id

    def _read_json(self, path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text())
        except Exception:
            return default

    def _read_text(self, path: Path) -> str | None:
        try:
            return path.read_text()
        except Exception:
            return None

    def list_cycles(self) -> list[str]:
        if not self.base_dir.exists():
            return []
        cycles = [
            p.name for p in self.base_dir.iterdir()
            if p.is_dir() and p.name.startswith("cycle_")
        ]
        return sorted(cycles)

    def latest_cycle_id(self) -> str | None:
        latest = self.base_dir / "latest"
        if latest.exists():
            try:
                resolved = latest.resolve()
                if resolved.is_dir() and resolved.name.startswith("cycle_"):
                    return resolved.name
            except Exception:
                pass
        cycles = self.list_cycles()
        return cycles[-1] if cycles else None

    def load_global_status(self) -> dict[str, Any]:
        state_dir = self.base_dir.parent / "state"
        return self._read_json(state_dir / "quant_cycle_status.json", {})

    def load_cycle(self, cycle_id: str | None = None) -> dict[str, Any]:
        target = cycle_id or self.latest_cycle_id()
        if not target:
            return {
                "cycle_id": None,
                "status": {},
                "realtime_summary": {},
                "historical_summary": {},
                "execution_quality": {},
                "scorecard": {},
                "failures": [],
                "analysis_summary": None,
                "change_candidates": None,
            }
        cycle_dir = self._cycle_path(target)
        if not cycle_dir.exists():
            return {
                "cycle_id": None,
                "status": {},
                "realtime_summary": {},
                "historical_summary": {},
                "execution_quality": {},
                "scorecard": {},
                "failures": [],
                "analysis_summary": None,
                "change_candidates": None,
            }
        return {
            "cycle_id": target,
            "status": self._read_json(cycle_dir / "status.json", {}),
            "realtime_summary": self._read_json(cycle_dir / "realtime_summary.json", {}),
            "historical_summary": self._read_json(cycle_dir / "historical_summary.json", {}),
            "execution_quality": self._read_json(cycle_dir / "execution_quality.json", {}),
            "scorecard": self._read_json(cycle_dir / "scorecard.json", {}),
            "failures": self._read_json(cycle_dir / "failure_report.json", []),
            "analysis_summary": self._read_text(cycle_dir / "analysis_summary.md"),
            "change_candidates": self._read_text(cycle_dir / "change_candidates.md"),
        }

    def build_posture(self) -> dict[str, Any]:
        global_status = self.load_global_status()
        latest = self.load_cycle()
        scorecard = latest.get("scorecard") or {}
        status = latest.get("status") or {}
        cycles = self.list_cycles()
        return {
            "running": bool(global_status.get("running")),
            "active_cycle_id": global_status.get("active_cycle_id"),
            "completed_cycles": len(cycles),
            "available_cycles": cycles,
            "latest_cycle_id": latest.get("cycle_id"),
            "latest_phase": status.get("phase"),
            "latest_verdict": scorecard.get("verdict"),
            "latest_blended_score": scorecard.get("blended_score", {}),
            "latest_started_at": status.get("started_at"),
            "latest_deadline_at": status.get("deadline_at"),
        }

    def load_autonomous_status(self) -> dict[str, Any]:
        state_dir = self.base_dir.parent / "state"
        return self._read_json(state_dir / "autonomous_loop_status.json", {})

    def latest_realtime_summary(self) -> dict[str, Any]:
        return self.load_cycle().get("realtime_summary") or {}

    def latest_historical_summary(self) -> dict[str, Any]:
        return self.load_cycle().get("historical_summary") or {}

    def latest_blended_score(self) -> dict[str, Any]:
        return self.load_cycle().get("scorecard") or {}

    def latest_failures(self) -> list[dict[str, Any]]:
        return self.load_cycle().get("failures") or []
