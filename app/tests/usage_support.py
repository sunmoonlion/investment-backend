"""假对面报用量用的：样子和真的 Codex 0.155.1 报的一样——这一次的，加这条线到现在的累计。

按 kimi-k3 的公开单价，`tokens=1000` 的一次调用正好是 0.01 元（输入 400、输出 20）。
累计是故意带上的：一次调用的用量是「累计比上一次多出来的」，谁要是拿累计本身当一步的
用量，测试里的金额就对不上。
"""

from __future__ import annotations

from typing import Any

MODEL = "kimi-k3"
FIELDS = ("inputTokens", "cachedInputTokens", "cacheWriteInputTokens", "outputTokens")


class UsageMeter:
    def __init__(self) -> None:
        self.totals: dict[str, dict[str, int]] = {}

    def report(self, thread: str, tokens: int) -> dict[str, Any]:
        last = {
            "inputTokens": tokens * 2 // 5,
            "cachedInputTokens": 0,
            "cacheWriteInputTokens": 0,
            "outputTokens": tokens // 50,
            "reasoningOutputTokens": 0,
        }
        last["totalTokens"] = last["inputTokens"] + last["outputTokens"]
        total = self.totals.setdefault(
            thread, dict.fromkeys((*FIELDS, "totalTokens"), 0)
        )
        for name in (*FIELDS, "totalTokens"):
            total[name] += last[name]
        return {"last": last, "total": dict(total), "modelContextWindow": 258400}
