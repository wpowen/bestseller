from __future__ import annotations

import json
import logging
import os
from typing import Any

from bestseller.services.publishing.base import PlatformAdapter

logger = logging.getLogger(__name__)


def _decrypt_credentials(encrypted: str) -> dict[str, Any]:
    enc_key = os.environ.get("BESTSELLER_ENCRYPTION_KEY")
    if not enc_key:
        raise RuntimeError(
            "BESTSELLER_ENCRYPTION_KEY is required to decrypt platform credentials but is not set"
        )
    from cryptography.fernet import Fernet  # noqa: PLC0415

    f = Fernet(enc_key.encode())
    decrypted = f.decrypt(encrypted.encode()).decode()
    return json.loads(decrypted)  # type: ignore[no-any-return]


def get_adapter(platform_type: str, credentials_encrypted: str | None, api_base_url: str | None) -> PlatformAdapter:
    """Factory: return the correct adapter for a platform_type."""
    creds: dict[str, Any] = {}
    if credentials_encrypted:
        creds = _decrypt_credentials(credentials_encrypted)

    if platform_type == "fanqie":
        from bestseller.services.publishing.adapters.fanqie import FanqieAdapter  # noqa: PLC0415
        return FanqieAdapter(credentials=creds, api_base_url=api_base_url)
    if platform_type == "qidian":
        from bestseller.services.publishing.adapters.qidian import QidianAdapter  # noqa: PLC0415
        return QidianAdapter(credentials=creds, api_base_url=api_base_url)
    if platform_type == "qimao":
        from bestseller.services.publishing.adapters.qimao import QimaoAdapter  # noqa: PLC0415
        return QimaoAdapter(credentials=creds, api_base_url=api_base_url)

    raise ValueError(f"Unknown platform_type: {platform_type!r}")
