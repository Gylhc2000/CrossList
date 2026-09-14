"""探针：不同 max_tokens 下 deepseek-v4-flash 生成 Listing 的稳定性。

用法：python _test_listing_budget.py <jobId> <max_tokens> [次数]
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.agent.nodes.listing import build_listing_prompt, _SYSTEM  # noqa: E402
from app.rules.platforms import MARKET_BY_KEY, PLATFORMS  # noqa: E402

JOB = sys.argv[1]
MAXT = int(sys.argv[2])
N = int(sys.argv[3]) if len(sys.argv) > 3 else 3


async def main() -> None:
    s = get_settings()
    snap = httpx.get(f"http://localhost:8000/api/jobs/{JOB}", timeout=30).json()
    card = snap["result"]["knowledge_card"]
    market = MARKET_BY_KEY["us"]
    rules = PLATFORMS["amazon"].rules
    prompt = build_listing_prompt(card, market, rules)
    body = {
        "model": s.llm_text_model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.6,
        "max_tokens": MAXT,
    }
    ok = 0
    async with httpx.AsyncClient(timeout=300.0) as c:
        for i in range(N):
            r = await c.post(
                f"{s.llm_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {s.llm_api_key}"},
                json=body,
            )
            d = r.json()
            ch = (d.get("choices") or [{}])[0]
            content = (ch.get("message") or {}).get("content") or ""
            usage = d.get("usage") or {}
            good = False
            try:
                obj = json.loads(content)
                good = bool(obj.get("title")) and bool(obj.get("bullet_points")) and bool(obj.get("description"))
            except Exception:  # noqa: BLE001
                good = False
            ok += 1 if good else 0
            print(f"  run{i + 1}: finish={ch.get('finish_reason')} ctok={usage.get('completion_tokens')} "
                  f"len={len(content)} complete={good}")
    print(f"max_tokens={MAXT}: 完整 {ok}/{N}")


asyncio.run(main())
