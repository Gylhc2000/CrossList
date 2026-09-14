"""A/B 测试：qwen3.6-flash vs deepseek-v4-flash 的 Listing 生成质量。

同一知识卡片、同一提示词、同一平台规则，两个模型各生成 kr/aliexpress 与
us/amazon 两个组合；客观指标用规则校验（长度/条数/禁用词/语言纯度），
主观指标用第三方裁判模型（glm-5.2）按 validate 同款口径打分。
只读生产代码，不写任何任务状态。
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

sys.path.insert(0, ".")

from app.agent.nodes.listing import generate_one
from app.agent.nodes.validate import _SYSTEM, _score_prompt
from app.core.config import Settings
from app.core.llm import LlmClient
from app.rules.platforms import MARKET_BY_KEY, PLATFORMS

CARD = {
    "product_name_zh": "奥特曼多关节可动手办",
    "product_name_en": "Ultra Hero Multi-Joint Action Figure",
    "category": "玩具/手办",
    "brand": "Generic",
    "core_selling_points": [
        "红银经典配色，涂装精细还原动画视觉形象",
        "手、脚、腰多关节可动，可摆出多种战斗姿势",
        "安全环保 PVC/ABS 材质，无异味耐摔",
        "胸甲细节精致，胸口彩色计时器可反光",
        "标准比例，可与同系列手办组合陈列",
    ],
    "specs": [
        {"name": "材质", "value": "PVC + ABS"},
        {"name": "高度", "value": "18cm"},
        {"name": "关节", "value": "多关节可动"},
        {"name": "适用年龄", "value": "6岁以上"},
        {"name": "包装", "value": "彩盒装"},
    ],
    "target_audience": ["儿童", "动漫收藏爱好者"],
    "usage_scenarios": ["儿童生日礼物", "书桌摆件", "收藏陈列"],
    "visual_features": {"color": "红色/银色", "material": "PVC", "shape": "人形", "style": "特摄英雄"},
    "image_prompt_subject": "a red and silver superhero action figure with multi-joint articulation, PVC material, 18cm, dynamic pose, studio lighting",
    "package_contents": "手办 x1，可替换手型 x2，支架 x1",
    "suggested_price": {"currency": "USD", "value": 19.99},
    "hs_keywords": ["action figure", "anime figure", "pvc toy", "collectible"],
}

MODELS = ["qwen3.6-flash", "deepseek-v4-flash"]
JUDGE = "glm-5.2"  # 与两个参赛模型都无关的第三方裁判
CASES = [("kr", "aliexpress"), ("us", "amazon")]


async def main() -> None:
    llm = LlmClient(Settings(), timeout=180.0)
    results = []

    for model in MODELS:
        for mk, pk in CASES:
            market, plat = MARKET_BY_KEY[mk], PLATFORMS[pk]
            tag = f"{model} × {plat.name}/{market.label}"
            t0 = time.perf_counter()
            try:
                listing = await generate_one(llm, CARD, market, plat.rules, model=model)
                err = None
            except Exception as e:
                print(f"[FAIL] {tag}: {e}", flush=True)
                results.append({"model": model, "platform": pk, "market": mk, "error": str(e)[:200]})
                continue
            dt = time.perf_counter() - t0

            issues = check_issues = None
            from app.rules.platforms import check_text

            issues = check_issues = check_text(
                listing, plat.rules, lang_code=market.lang_code, language=market.language
            )
            errs = [i for i in issues if i.level == "error"]
            warns = [i for i in issues if i.level == "warn"]

            # 裁判依序降级：与两个参赛模型均无关，谁返回合法 JSON 用谁
            q = None
            for judge in ("glm-5.2", "kimi-k2.6", "qwen3.8-max"):
                try:
                    q = await llm.chat_json(
                        [
                            {"role": "system", "content": _SYSTEM},
                            {"role": "user", "content": _score_prompt(plat, market, listing, CARD)},
                        ],
                        model=judge,
                        temperature=0.2,
                        max_tokens=800,
                    )
                    if isinstance(q.get("score"), int):
                        break
                    q = None
                except Exception as e:
                    print(f"  [裁判 {judge} 失败，换下一个] {str(e)[:80]}", flush=True)

            rec = {
                "model": model,
                "platform": plat.name,
                "market": f"{market.flag}{market.label}",
                "lang": market.lang_code,
                "gen_seconds": round(dt, 1),
                "rule_errors": [f"[{i.field}] {i.msg}" for i in errs],
                "rule_warns": [f"[{i.field}] {i.msg}" for i in warns],
                "judge_score": q.get("score"),
                "judge_comments": q.get("comments"),
                "judge_suggestions": q.get("suggestions"),
                "listing": listing,
            }
            results.append(rec)
            print(
                f"[OK] {tag} · {dt:.0f}s · 规则error {len(errs)} · 裁判 {q.get('score')} 分",
                flush=True,
            )

    with open("_ab_result.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 汇总表
    print("\n===== 汇总 =====")
    print(f"{'模型':<20}{'组合':<22}{'耗时':>7}{'err':>5}{'warn':>6}{'裁判分':>7}")
    for r in results:
        if "error" in r:
            print(f"{r['model']:<20}{r['platform']:<22}{'FAIL'}")
            continue
        print(
            f"{r['model']:<20}{r['platform']+'/'+r['market']:<22}"
            f"{r['gen_seconds']:>6.0f}s{len(r['rule_errors']):>5}{len(r['rule_warns']):>6}"
            f"{r['judge_score']:>7}"
        )


if __name__ == "__main__":
    asyncio.run(main())
