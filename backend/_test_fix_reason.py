"""探针：复现「首轮校验报错 → 触发 1 轮修正」的原因。

背景：用户跑了一个 10 站点任务，其中 4 个站点首轮各报 1 项不合规，于是各跑了 1 轮修正。
但**快照只保留最终结果，当轮的不合规详情没有落盘**（日志只写"第 N 轮修正完成"），
所以无法事后归因。本探针拿那次任务的知识卡片，对同样的 (平台, 市场) 重新生成并校验，
把当轮的 error 级问题打出来。

用法：python _test_fix_reason.py <job_id> [pk:mk ...] [--n 3]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from app.agent.nodes.listing import generate_one  # noqa: E402
from app.agent.nodes.validate import MAX_TOKENS_SCORE, _SYSTEM, _score_prompt  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.llm import LlmClient  # noqa: E402
from app.rules.platforms import MARKET_BY_KEY, PLATFORMS, check_text  # noqa: E402


async def main() -> None:
    job = sys.argv[1]
    argv = sys.argv[2:]
    n = 3
    if "--n" in argv:
        i = argv.index("--n")
        n = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    targets = argv or ["amazon:us", "amazon:br", "shopee:br", "tiktok:de"]

    snap = httpx.get(f"http://localhost:8000/api/jobs/{job}", timeout=30).json()
    card = (snap.get("result") or {}).get("knowledge_card") or {}
    if not card:
        print("卡片取不到，退出")
        return
    print(f"卡片来自 {job}；规格 {len(card.get('specs') or [])} 项；"
          f"外观 {card.get('visual_features') or '—'}\n")

    s = get_settings()
    llm = LlmClient(s)
    for t in targets:
        pk, mk = t.split(":")
        p, market = PLATFORMS[pk], MARKET_BY_KEY[mk]
        print(f"===== {p.name} × {market.label}（{market.language}）=====")
        for i in range(1, n + 1):
            listing = await generate_one(llm, card, market, p.rules)
            issues = check_text(listing, p.rules, lang_code=market.lang_code, language=market.language)
            q = await llm.chat_json(
                [{"role": "system", "content": _SYSTEM},
                 {"role": "user", "content": _score_prompt(p, market, listing, card)}],
                temperature=0.2, max_tokens=MAX_TOKENS_SCORE,
            )
            errs = [(x.level, x.field, x.msg) for x in issues if x.level == "error"]
            for f in (q.get("issues") or [])[:5]:
                if isinstance(f, dict) and f.get("claim"):
                    errs.append(("error", "编造卖点", f"{f.get('claim')} → {f.get('fix','')}"))
            print(f"  [{i}] 分={q.get('score')} 字数: title={len(listing['title'])} "
                  f"bullet={[len(b) for b in listing['bullet_points']]} desc={len(listing['description'])}")
            if errs:
                for lv, fd, msg in errs:
                    print(f"      ✗ {fd}: {msg[:100]}")
            else:
                print("      ✓ 无 error 级问题（不会触发修正轮）")
        print()


asyncio.run(main())
