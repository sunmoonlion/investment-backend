from __future__ import annotations

from typing import Any

from app.domain.agent.models import StoredMessage

CURRENT_STORED_MESSAGE_SCHEMA_VERSION = 1


def upcast_stored_message(raw: dict[str, Any]) -> StoredMessage:
    version = int(raw.get("schema_version") or 1)
    if version > CURRENT_STORED_MESSAGE_SCHEMA_VERSION:
        raise ValueError(f"unsupported StoredMessage schema_version: {version}")
    if version == 1:
        return StoredMessage.model_validate(raw)
    raise ValueError(f"unsupported StoredMessage schema_version: {version}")
