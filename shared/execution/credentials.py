"""API 키 로더 — env var 우선, 암호화된 로컬 파일 fallback.

스크립트(bar_scheduler, risk_daemon, execute_signals)에서 키가 필요할 때,
매번 커맨드라인에 평문 노출 대신 안전한 경로 사용:

  1. 환경변수 UPBIT_API_KEY / UPBIT_API_SECRET
  2. ~/.quant/credentials.enc (Fernet 암호화, 마스터키는 별도 env)

마스터키 (QUANT_MASTER_KEY)는 openssl rand -hex 32로 생성 후 쉘 프로필에:
  export QUANT_MASTER_KEY=<hex>

사용:
  from shared.execution.credentials import load_credentials
  key, secret = load_credentials("upbit")
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

try:
    from cryptography.fernet import Fernet
except Exception:  # cryptography not installed
    Fernet = None  # type: ignore


CRED_DIR = Path.home() / ".quant"
CRED_FILE = CRED_DIR / "credentials.enc"


def _derive_fernet_key(master: str) -> bytes:
    """마스터 hex를 Fernet 호환 키로 변환 (32-byte SHA256 → base64)."""
    digest = hashlib.sha256(master.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _load_file_credentials() -> dict[str, dict[str, str]]:
    """파일에서 복호화된 credential dict 반환. 파일 없으면 빈 dict."""
    if not CRED_FILE.exists():
        return {}
    master = os.getenv("QUANT_MASTER_KEY")
    if not master:
        return {}
    if Fernet is None:
        return {}
    try:
        f = Fernet(_derive_fernet_key(master))
        plaintext = f.decrypt(CRED_FILE.read_bytes()).decode()
        return json.loads(plaintext)
    except Exception:
        return {}


def save_credentials(exchange: str, api_key: str, api_secret: str) -> None:
    """로컬 파일에 암호화 저장. QUANT_MASTER_KEY 필요."""
    if Fernet is None:
        raise RuntimeError("cryptography 패키지 필요: pip install cryptography")
    master = os.getenv("QUANT_MASTER_KEY")
    if not master:
        raise RuntimeError(
            "QUANT_MASTER_KEY 환경변수 미설정. "
            "다음 명령으로 생성:\n  export QUANT_MASTER_KEY=$(openssl rand -hex 32)"
        )

    CRED_DIR.mkdir(parents=True, exist_ok=True)
    CRED_DIR.chmod(0o700)

    existing = _load_file_credentials()
    existing[exchange] = {"api_key": api_key, "api_secret": api_secret}

    f = Fernet(_derive_fernet_key(master))
    encrypted = f.encrypt(json.dumps(existing).encode())
    CRED_FILE.write_bytes(encrypted)
    CRED_FILE.chmod(0o600)


def load_credentials(exchange: str) -> tuple[str, str]:
    """(api_key, api_secret) 반환. env → file 순으로 조회."""
    exchange = exchange.lower()
    env_key = f"{exchange.upper()}_API_KEY"
    env_secret = f"{exchange.upper()}_API_SECRET"

    key = os.getenv(env_key, "")
    secret = os.getenv(env_secret, "")

    if key and secret:
        return key, secret

    # Fallback: 파일
    file_creds = _load_file_credentials()
    if exchange in file_creds:
        return file_creds[exchange]["api_key"], file_creds[exchange]["api_secret"]

    raise RuntimeError(
        f"{exchange} 자격증명 없음. 다음 중 하나로 설정:\n"
        f"  1. export {env_key}=...; export {env_secret}=...\n"
        f"  2. python -m shared.execution.credentials save {exchange} <key> <secret>"
    )


def validate_upbit(api_key: str, api_secret: str) -> bool:
    """Upbit API 키 유효성 검증."""
    from shared.execution.upbit import UpbitConnector
    try:
        conn = UpbitConnector(api_key, api_secret)
        return conn.validate_credentials()
    except Exception:
        return False


def _cli():
    import argparse
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_save = sub.add_parser("save", help="키 저장")
    sp_save.add_argument("exchange")
    sp_save.add_argument("api_key")
    sp_save.add_argument("api_secret")
    sp_save.add_argument("--validate", action="store_true",
                        help="저장 전 키 유효성 검증 (Upbit 한정)")

    sp_list = sub.add_parser("list", help="저장된 거래소 목록")

    sp_del = sub.add_parser("delete", help="키 삭제")
    sp_del.add_argument("exchange")

    args = p.parse_args()

    if args.cmd == "save":
        if args.validate and args.exchange.lower() == "upbit":
            print(f"  Upbit 키 유효성 검증 중...")
            if not validate_upbit(args.api_key, args.api_secret):
                print("  ❌ 검증 실패 — 키가 잘못됐거나 권한 부족")
                return 1
            print("  ✓ 검증 성공")
        save_credentials(args.exchange, args.api_key, args.api_secret)
        print(f"  ✓ {args.exchange} 키 저장 → {CRED_FILE}")

    elif args.cmd == "list":
        creds = _load_file_credentials()
        if not creds:
            print("  저장된 키 없음 (또는 QUANT_MASTER_KEY 미설정)")
        else:
            print(f"  저장된 거래소:")
            for ex in creds:
                key = creds[ex]["api_key"]
                print(f"    - {ex}: {key[:8]}...{key[-4:]}")

    elif args.cmd == "delete":
        creds = _load_file_credentials()
        if args.exchange in creds:
            del creds[args.exchange]
            master = os.getenv("QUANT_MASTER_KEY", "")
            f = Fernet(_derive_fernet_key(master))
            CRED_FILE.write_bytes(f.encrypt(json.dumps(creds).encode()))
            print(f"  ✓ {args.exchange} 삭제")
        else:
            print(f"  {args.exchange} 없음")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli() or 0)
