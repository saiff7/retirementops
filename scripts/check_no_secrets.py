"""Fail if git tracks secret-bearing files or obvious secret material.

A lightweight guard, not a full scanner such as gitleaks: it checks tracked paths and a few
high-signal patterns. Run locally or in CI: `python3 scripts/check_no_secrets.py`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_PATHS = re.compile(
    r"(^|/)(\.env(\..+)?|\.dev\.vars|\.mcp\.json|keys/.*|.*\.pem|.*\.key)$"
)
ALLOWED_PATHS = {".env.example", ".mcp.json.example"}
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "anthropic key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "aws access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "github token": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "jwt": re.compile(r"eyJ[\w-]{10,}\.eyJ[\w-]{10,}\.[\w-]{10,}"),
}


def main() -> int:
    files = (
        subprocess.run(  # fixed argv, no shell
            ["git", "ls-files", "-z"],  # noqa: S607 - git from PATH is intended
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .split("\0")
    )
    problems: list[str] = []
    for rel in filter(None, files):
        if rel not in ALLOWED_PATHS and FORBIDDEN_PATHS.search(rel):
            problems.append(f"{rel}: secret-bearing file must not be tracked")
            continue
        if rel == "scripts/check_no_secrets.py":
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        problems += [
            f"{rel}: looks like {name}" for name, rx in PATTERNS.items() if rx.search(text)
        ]
    for p in problems:
        print(f"SECRET CHECK FAILED: {p}")
    if not problems:
        print(f"secret check passed ({len([f for f in files if f])} tracked files)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
