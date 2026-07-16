"""Fail CI when private operations or implementation details enter the public tree."""

from __future__ import annotations

import re
from pathlib import Path

SCANNER = Path(__file__).resolve()
ROOT = SCANNER.parents[1]
SKIP = {".git", ".venv", "dist", "build"}
DENIED_PATHS = {
    "CLAUDE.md",
    "CLAUDE.local.md",
    "OPERATOR_CONTEXT.md",
    "INSTANCE.md",
}
DENIED_PATTERNS = {
    "private package import": re.compile(r"\b(?:quant_alpha|quant_ops)\b"),
    "operator home path": re.compile(r"/home/(?:ubuntu|root)/"),
    "private key path": re.compile(r"\.ssh/[^\s]+"),
    "runtime portfolio state": re.compile(r"portfolio_(?:state|history)"),
    "credential file": re.compile(r"\.git-credentials|BEGIN (?:RSA |OPENSSH )?PRIVATE KEY"),
    "IPv4 literal": re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),
}
ALLOWED_IPV4 = {"0.0.0.0", "127.0.0.1"}


def main() -> int:
    failures: list[str] = []
    for path in ROOT.rglob("*"):
        if path.resolve() == SCANNER:
            continue
        if not path.is_file() or any(part in SKIP for part in path.parts):
            continue
        rel = path.relative_to(ROOT)
        if rel.name in DENIED_PATHS or rel.name.endswith(".private.md"):
            failures.append(f"denied path: {rel}")
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in DENIED_PATTERNS.items():
            for match in pattern.finditer(text):
                value = match.group(0)
                if label == "IPv4 literal" and value in ALLOWED_IPV4:
                    continue
                failures.append(f"{rel}: {label}: {value}")
    if failures:
        print("Public-boundary violations detected:")
        print("\n".join(f"- {item}" for item in failures))
        return 1
    print("Public boundary check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
