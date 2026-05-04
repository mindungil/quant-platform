"""Tests for scripts/virtual/ operational tools (init/status/reset).

We exercise them via subprocess + isolated tmp data dirs (since they
all operate on the project-level data/virtual/ path).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VIRT_STATE = REPO_ROOT / "data" / "virtual" / "state.json"


def _run(*args, input_text=None):
    return subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        input=input_text,
        timeout=30,
    )


@pytest.fixture
def preserve_state():
    """Save and restore data/virtual/ across each test."""
    backup = None
    if VIRT_STATE.exists():
        backup = VIRT_STATE.read_text()
    yield
    if backup is not None:
        VIRT_STATE.write_text(backup)
    elif VIRT_STATE.exists():
        VIRT_STATE.unlink()


def test_init_script_creates_state(preserve_state):
    # Remove existing first
    if VIRT_STATE.exists():
        VIRT_STATE.unlink()
    r = _run("scripts/virtual/init.py", "--equity", "12345")
    assert r.returncode == 0, r.stderr
    assert VIRT_STATE.exists()
    data = json.loads(VIRT_STATE.read_text())
    assert data["equity"] == pytest.approx(12345)


def test_init_script_refuses_existing_without_force(preserve_state):
    # Ensure state exists
    _run("scripts/virtual/init.py", "--equity", "10000")
    # Try to re-init without --force
    r = _run("scripts/virtual/init.py", "--equity", "99999")
    assert r.returncode != 0
    assert "Refusing" in r.stdout or "Refusing" in r.stderr


def test_init_script_force_overwrites(preserve_state):
    _run("scripts/virtual/init.py", "--equity", "10000")
    r = _run("scripts/virtual/init.py", "--equity", "20000", "--force")
    assert r.returncode == 0
    data = json.loads(VIRT_STATE.read_text())
    assert data["equity"] == pytest.approx(20000)


def test_status_script_prints_equity(preserve_state):
    _run("scripts/virtual/init.py", "--equity", "10000", "--force")
    r = _run("scripts/virtual/status.py")
    assert r.returncode == 0
    assert "Equity" in r.stdout
    assert "10,000" in r.stdout or "10000" in r.stdout


def test_status_json_mode_is_parseable(preserve_state):
    _run("scripts/virtual/init.py", "--equity", "10000", "--force")
    r = _run("scripts/virtual/status.py", "--json")
    assert r.returncode == 0
    obj = json.loads(r.stdout)
    assert "equity" in obj
    assert obj["equity"] == pytest.approx(10000)


def test_reset_script_requires_confirmation(preserve_state):
    _run("scripts/virtual/init.py", "--equity", "10000", "--force")
    # Decline
    r = _run("scripts/virtual/reset.py", "--equity", "1000", input_text="no\n")
    assert r.returncode != 0
    data = json.loads(VIRT_STATE.read_text())
    assert data["equity"] == pytest.approx(10000)


def test_reset_script_yes_flag_works(preserve_state):
    _run("scripts/virtual/init.py", "--equity", "10000", "--force")
    r = _run("scripts/virtual/reset.py", "--equity", "5000", "--yes")
    assert r.returncode == 0
    data = json.loads(VIRT_STATE.read_text())
    assert data["equity"] == pytest.approx(5000)
