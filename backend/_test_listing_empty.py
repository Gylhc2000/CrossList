"""探针：复现「模型未返回合法 JSON：（空）」并对比不同参数。

关键线索：这条错误只在 `extract_json` 里抛出，而它会先剥掉 <think>…</think>。
实测 deepseek-v4-flash 会**把全部 max_tokens 烧在思维链上、content 返回空串**，
与 chat() 里的「200 但 content 为空」是同一类现象的不同表现。
把上限调大并不能修复它，只会让每次失败更慢（8000 时单次要 100 秒以上）。

用法：python _test_listing_empty.py [模型] [次数] [max_tokens] [extra_json]
  python _test_listing_empty.py deepseek-v4-flash 4 4000
  python _test_listing_empty.py deepseek-v4-flash 4 4000 '{"enable_thinking": false}'
  python _test_listing_empty.py qwen3.6-flash 4 4000
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from app.agent.nodes.listing import MAX_TOKENS_LISTING, _SYSTEM, build_listing_prompt  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.rules.platforms import MARKET_BY_KEY, PLATFORMS  # noqa: E402

CARD = {
    "product_name_zh": "真无线降噪蓝牙耳机",
    "product_name_en": "True Wireless Earbuds TWS 5.3 Active Noise Cancelling",
    "category": "3C数码 / 耳机",
    "brand": "Generic",
    "core_selling_points": [
        "蓝牙5.3稳定连接，低延迟",
        "主动降噪，通勤安静",
        "充电仓400mAh，持久续航",
        "单次续航6小时，全天候使用",
        "入耳式贴合，久戴舒适",
    ],
    "specs": [
        {"name": "充电仓容量", "value": "400mAh"},
        {"name": "单次续航", "value": "6小时"},
        {"name": "蓝牙", "value": "5.3"},
    ],
    "target_audience": ["通勤人群", "学生"],
    "usage_scenarios": ["地铁通勤", "办公", "运动"],
    "visual_features": {},
    "package_contents": "未提供",
    "suggested_price": {"currency": "USD", "value": 29.99},
    "hs_keywords": ["wireless earbuds"],
}


async def once(
    client: httpx.AsyncClient, s, model: str, market_key: str, maxtok: int, extra: dict, i: int
) -> tuple[bool, bool, float]:
    """返回 (是否拿到完整 JSON, 是否烧光预算, 耗时秒)"""
    market = MARKET_BY_KEY[market_key]
    rules = PLATFORMS["shopee"].rules
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": build_listing_prompt(CARD, market, rules)},
        ],
        "temperature": 0.6,
        "max_tokens": maxtok,
        **extra,
    }
    t0 = time.perf_counter()
    r = await client.post(
        f"{s.llm_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {s.llm_api_key}"},
        json=body,
    )
    dt = time.perf_counter() - t0
    d = r.json()
    if d.get("code"):
        print(f"  [{i}] HTTP {r.status_code} code={d.get('code')} {str(d.get('message'))[:110]}  {dt:.0f}s")
        return False, False, dt
    ch = (d.get("choices") or [{}])[0]
    raw = (ch.get("message") or {}).get("content") or ""
    usage = d.get("usage") or {}
    ctok = usage.get("completion_tokens")
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.S | re.I).strip().strip("`")
    ok = False
    if cleaned.startswith("{") or cleaned.startswith("```"):
        try:
            json.loads(cleaned.split("```")[1] if cleaned.startswith("```") else cleaned)
            ok = True
        except Exception:
            ok = False
    burned = raw == ""
    print(
        f"  [{i}] finish={str(ch.get('finish_reason')):6} ctok={ctok:>5} raw_len={len(raw):>5} "
        f"完整JSON={ok!s:5} 空正文={burned!s:5} {dt:.0f}s"
        + ("" if raw else "   ← 全部预算烧在思维链上")
    )
    return ok, burned, dt


async def main() -> None:
    s = get_settings()
    model = sys.argv[1] if len(sys.argv) > 1 else s.llm_text_model
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    maxtok = int(sys.argv[3]) if len(sys.argv) > 3 else MAX_TOKENS_LISTING
    extra = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
    print(f"model={model}  max_tokens={maxtok}  extra={extra}  market=kr(韩语)  n={n}\n")
    res = []
    async with httpx.AsyncClient(timeout=600.0) as c:
        for i in range(1, n + 1):
            res.append(await once(c, s, model, "kr", maxtok, extra, i))
    okn = sum(1 for r in res if r[0])
    empty = sum(1 for r in res if r[1])
    avg = sum(r[2] for r in res) / max(len(res), 1)
    print(f"\n==> 完整 JSON {okn}/{n} · 空正文 {empty}/{n} · 平均耗时 {avg:.0f}s")


asyncio.run(main())
