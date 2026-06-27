"""
Symmetric encryption helpers for AgentShield Registry API.

Used exclusively for LLM provider credentials stored in Postgres.
Postgres is the source of truth; K8s Secrets are derived artifacts written
at deploy time.

Key management
--------------
Set AGENTSHIELD_ENCRYPTION_KEY to a Fernet key (URL-safe base64, 32 bytes).
Generate one with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Store the key in a K8s Secret and reference it via secretKeyRef in the
registry-api Deployment — never hard-code it.
"""

import json
import os

from cryptography.fernet import Fernet, InvalidToken


def _fernet() -> Fernet:
    key = os.environ.get("AGENTSHIELD_ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError(
            "AGENTSHIELD_ENCRYPTION_KEY is not set. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_json(data: dict) -> str:
    """Encrypt a dict to a Fernet token string."""
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str) -> dict:
    """Decrypt a Fernet token string back to a dict."""
    try:
        return json.loads(_fernet().decrypt(token.encode()).decode())
    except (InvalidToken, ValueError) as exc:
        raise RuntimeError("Failed to decrypt credentials — wrong key or corrupted data.") from exc
