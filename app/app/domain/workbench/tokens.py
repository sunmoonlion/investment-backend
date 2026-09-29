"""待办的答复令牌：库里只存摘要，不存令牌本身。"""

from __future__ import annotations

import hashlib


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
