"""交回物里的字段，给用户看的叫法。

交回物的结构是专家包定的，字段名是英文（给模型和验收用的）。页面上「交回了什么」不该让用户看英文字段名。
这里只登记叫法；没登记的字段，页面照原名显示。纯数据，不碰数据库。
"""

from __future__ import annotations

from typing import Any

FIELD_WORDS: dict[str, str] = {
    # 定范围、改写问题
    "company": "公司",
    "security_code": "证券代码",
    "periods": "期间",
    "period": "期间",
    "report_types": "报告类型",
    "report_type": "报告类型",
    "aspects": "关注的方面",
    "assumptions": "假设",
    "question": "问题",
    "entities": "对象",
    # 数据摸底
    "dataset": "数据集",
    "not_ingested": "没有入库",
    "data_version": "数据版本",
    "as_of": "数据到",
    "notes": "说明",
    "fiscal_year": "年度",
    "report_date": "报告期",
    "basis": "口径",
    "verified": "核对过",
    "official_disclosed_date": "官方披露日",
    "basis_breaks": "口径断点",
    "between": "在哪两期之间",
    # 勾稽
    "checks": "勾稽结果",
    "rule_id": "编号",
    "rule": "规则",
    "periods_checked": "验了几期",
    "unbalanced": "不平的期数",
    "unbalanced_periods": "不平的期间",
    "continuity": "跨期连续性",
    "diff": "差额",
    "note": "说明",
    "unexplained_breaks": "没有解释的断点",
    # 取数、算指标、问数
    "table": "表",
    "tables": "表",
    "sql": "查询语句",
    "statement": "报表",
    "item": "科目",
    "display_name": "名称",
    "value": "数值",
    "unit": "单位",
    "metric_name": "指标",
    "metrics": "指标",
    "formula": "算法",
    "inputs": "用到的数",
    "applicable": "适用",
    "reason_if_not": "不适用的原因",
    "source": "来源",
    "columns": "列",
    "name": "名字",
    "definition": "口径",
    "rows": "结果",
    "row_count": "行数",
    # 对照官方
    "matched": "一致的",
    "mismatched": "对不上的",
    "not_covered": "没有对照到的",
    "coverage": "覆盖说明",
    "official": "公司披露的",
    "source_report": "出自",
    "page": "页码",
    # 成稿
    "answer": "回答",
    "title": "标题",
    "observations": "观察",
    "caveats": "提醒",
    "limitations": "局限",
    "citations": "出处",
    "conclusion": "结论",
}


def labels_of(content: Any) -> dict[str, str]:
    """这份交回物里出现过的字段各叫什么。只给登记过的。"""
    found: dict[str, str] = {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                if key in FIELD_WORDS:
                    found[key] = FIELD_WORDS[key]
                walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)

    walk(content)
    return found
