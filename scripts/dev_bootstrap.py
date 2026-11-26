"""Create local-only secrets for development.

- Writes `.env` from `.env.example`, replacing every `__generate__` with a random value.
  It never overwrites an existing `.env`.
- Generates an RS256 keypair for the mock identity provider in `keys/`. Existing keys are kept.

Everything this script writes is git-ignored. Nothing here is a production secret.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parent.parent


def write_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        # Never overwrite existing values; only append keys added to .env.example since.
        present = {ln.split("=", 1)[0] for ln in env.read_text().splitlines() if "=" in ln}
        missing = [
            ln.split("=", 1)[0]
            for ln in (ROOT / ".env.example").read_text().splitlines()
            if ln.endswith("=__generate__") and ln.split("=", 1)[0] not in present
        ]
        if missing:
            with env.open("a") as fh:
                fh.writelines(f"{k}={secrets.token_urlsafe(32)}\n" for k in missing)
            print(f".env exists; appended new keys: {', '.join(missing)}")
        else:
            print(".env exists; leaving it unchanged")
        return
    lines = []
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.endswith("=__generate__"):
            key = line.split("=", 1)[0]
            line = f"{key}={secrets.token_urlsafe(32)}"
        lines.append(line)
    env.write_text("\n".join(lines) + "\n")
    env.chmod(0o600)
    print("wrote .env (mode 600)")


def write_keys() -> None:
    keys = ROOT / "keys"
    keys.mkdir(exist_ok=True)
    private, public = keys / "dev_jwt_private.pem", keys / "dev_jwt_public.pem"
    if private.exists() and public.exists():
        print("dev JWT keypair exists; leaving it unchanged")
        return
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private.chmod(0o600)
    public.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    print("wrote keys/dev_jwt_{private,public}.pem")


if __name__ == "__main__":
    write_env()
    write_keys()
    sys.exit(0)
