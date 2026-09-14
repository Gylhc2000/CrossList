"""CSV 批量上传模板生成 —— pandas

对应方案「格式生成：pandas（CSV生成）」。
输出 utf-8-sig（带 BOM），保证 Excel 打开中文不乱码。
"""
from __future__ import annotations

import io

import pandas as pd


def build_csv(columns: list[str], row: dict[str, object]) -> bytes:
    """按模板列序生成单行 CSV 的二进制内容"""
    record = {c: _normalize(row.get(c, "")) for c in columns}
    df = pd.DataFrame([record], columns=columns)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8-sig")


def build_csv_multi(columns: list[str], rows: list[dict[str, object]]) -> bytes:
    """多 SKU / 多变体场景"""
    records = [{c: _normalize(r.get(c, "")) for c in columns} for r in rows]
    df = pd.DataFrame(records, columns=columns)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8-sig")


def _normalize(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ";".join(str(x) for x in v)
    return str(v)
