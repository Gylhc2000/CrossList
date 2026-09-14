"""探针：Listing 生成是否偶发「只返回标题」的部分 JSON（疑似 max_tokens 截断）。

用法：python _test_listing_partial.py <jobId> [次数]
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.llm import LlmClient  # noqa: E402
from app.agent.nodes.listing import build_listing_prompt, MAX_TOKENS_LISTING, _SYSTEM  # noqa: E402
from app.rules.platforms import MARKET_BY_KEY, PLATFORMS  # noqa: E402

JOB = sys.argv[1] if len(sys.argv) > 1 else "job_3dee3381ca3c"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 3


async def main() -> None:
    s = get_settings()
    snap = httpx.get(f"http://localhost:8000/api/jobs/{JOB}", timeout=30).json()
    card = snap["result"]["knowledge_card"]
    print("card product:", card.get("product_name_en"))

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
        "max_tokens": MAX_TOKENS_LISTING,
    }
    async with httpx.AsyncClient(timeout=180.0) as c:
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
            print(f"\n--- run {i + 1} ---")
            print("  finish_reason:", ch.get("finish_reason"))
            print("  completion_tokens:", usage.get("completion_tokens"), "/ limit", MAX_TOKENS_LISTING)
            print("  content_len:", len(content))
            try:
                obj = json.loads(content)
                print("  parsed keys:", list(obj.keys()))
                print("  title_len:", len(str(obj.get("title") or "")),
                      "bullets:", len(obj.get("bullet_points") or []),
                      "desc_len:", len(str(obj.get("description") or "")))
            except Exception as e:  # noqa: BLE001
                print("  json.loads FAILED:", str(e)[:80])
                print("  tail:", repr(content[-160:]))


asyncio.run(main())
