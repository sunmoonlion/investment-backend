"""确定性验收（F-ACCEPT-01 的第一层）：纯函数，输入交回物文本与规则，输出 pass/fail 与原因。不调模型。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.domain.workbench.packs import AcceptanceRule

# F-POS-04：对具体证券的评级、目标价、买卖建议措辞，云端确定性拦截并留痕
_POSITIONING = re.compile(
    r"(强烈?(买入|卖出|增持|减持|推荐)|目标价|建议(买入|卖出|建仓|清仓|加仓|减仓)|(买入|卖出)评级|必涨|必跌|稳赚|保本收益|strong (buy|sell)|price target|buy rating|sell rating)",
    re.I,
)


@dataclass
class Verdict:
    ok: bool
    parsed: Any = None
    failures: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)


def _extract_json(text: str) -> Any:
    """容忍 ```json 围栏与前后闲话：取第一个平衡的 {...}。"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start = t.find("{")
    depth = 0
    for i in range(start, len(t)) if start >= 0 else []:
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start : i + 1])
                except json.JSONDecodeError:
                    break
    raise ValueError("no JSON object found")


def _get(obj: Any, path: str | None) -> Any:
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _text_of(value: Any) -> str:
    """要查措辞的文字：字符串原样；列表与对象取其中所有的字符串（键名不算）。"""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_text_of(v) for v in value.values())
    if isinstance(value, list):
        return "\n".join(_text_of(v) for v in value)
    return ""


def _same(left: Any, right: Any) -> bool:
    # True == 1 在 Python 里成立；验收里布尔和数字不是一回事
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, int | float) and isinstance(right, int | float):
        return float(left) == float(right)
    return type(left) is type(right) and left == right


def _all_equal(value: Any, name: str | None, expected: Any) -> tuple[bool, str]:
    """列表的每一项都有这个字段且等于期望值。空列表算通过：要求非空另用 list_min。"""
    if not isinstance(value, list):
        return False, "not a list"
    if not name:
        return False, "rule has no field"
    bad = [
        i
        for i, item in enumerate(value)
        if not isinstance(item, dict)
        or name not in item
        or not _same(item[name], expected)
    ]
    return not bad, f"items {bad[:10]} differ" if bad else f"len={len(value)}"


def judge(text: str | None, rules: tuple[AcceptanceRule, ...]) -> Verdict:
    v = Verdict(ok=True)
    parsed: Any = None
    needs_json = any(r.kind != "max_chars" for r in rules)
    if needs_json:
        try:
            parsed = _extract_json(text or "")
            if not isinstance(parsed, dict):
                raise ValueError("top level is not an object")
        except ValueError as exc:
            v.ok = False
            v.failures.append(f"json_object: {exc}")
            v.checks.append({"rule": "json_object", "pass": False, "detail": str(exc)})
            return v
    v.parsed = parsed
    for r in rules:
        ok, detail = True, ""
        if r.kind == "json_object":
            ok = isinstance(parsed, dict)
        elif r.kind == "required_keys":
            missing = [k for k in r.keys if k not in parsed]
            ok, detail = not missing, f"missing {missing}" if missing else ""
        elif r.kind == "non_empty":
            val = _get(parsed, r.path)
            ok = bool(val) and (not isinstance(val, str) or val.strip() != "")
        elif r.kind == "list_min":
            val = _get(parsed, r.path)
            ok = isinstance(val, list) and len(val) >= r.min_items
            detail = f"len={len(val) if isinstance(val, list) else 'n/a'}"
        elif r.kind == "no_positioning_advice":
            val = _get(parsed, r.path)
            hit = _POSITIONING.search(_text_of(val))
            ok, detail = hit is None, (hit.group(0) if hit else "")
        elif r.kind == "max_chars":
            ok = len(text or "") <= r.max_chars
        elif r.kind == "all_equal":
            ok, detail = _all_equal(_get(parsed, r.path), r.field, r.equals)
        elif r.kind == "list_empty":
            val = _get(parsed, r.path)
            ok = isinstance(val, list) and not val
            detail = f"len={len(val)}" if isinstance(val, list) else "not a list"
        elif r.kind == "blank":
            val = _get(parsed, r.path)
            ok = val is None or (isinstance(val, str) and val.strip() == "")
            detail = "" if ok else "has content"
        v.checks.append({"rule": r.kind, "path": r.path, "pass": ok, "detail": detail})
        if not ok:
            v.ok = False
            v.failures.append(
                f"{r.kind}{'(' + r.path + ')' if r.path else ''}: {r.message or detail}"
            )
    return v
