"""节点：多平台规则校验 + 语言质量评分

对应方案「模块F：质量校验Agent」。
校验维度：合规性（规则库）+ 语言质量（LLM 自评）+ 信息一致性。
校验结果写入 state["issues"] / state["quality"]，供条件边判定是否触发修正。
"""
from __future__ import annotations

import asyncio
import json

from app.agent.emitter import S_CHECK
from app.core.llm import LlmClient
from app.rules.platforms import (
    MARKET_BY_LANG,
    PLATFORMS,
    Issue,
    check_text,
)

_SYSTEM = "你是跨境电商 Listing 质量审核专家。只输出严格合法的 JSON 对象。"

CONCURRENCY = 4

# deepseek 的评分输出（中文点评+建议）比 qwen 啰嗦，800 会把 JSON 截断导致解析失败
MAX_TOKENS_SCORE = 2000


def _card_digest(card: dict) -> str:
    """只把评分真正需要的字段传给模型，避免每轮重发整张知识卡片"""
    specs = card.get("specs") or []
    return json.dumps(
        {
            "product_name": card.get("product_name_en") or card.get("product_name_zh"),
            "category": card.get("category"),
            "core_selling_points": card.get("core_selling_points") or [],
            "specs": [f"{s.get('name')}={s.get('value')}" for s in specs][:8],
        },
        ensure_ascii=False,
    )


def _score_prompt(platform, market, listing: dict, card: dict) -> str:
    return f"""请以{platform.name}平台标准审核下面这条{market.language} Listing，给出语言质量评分。

平台：{platform.name}
商品参考信息：
{_card_digest(card)}

Listing 内容：
标题：{listing.get("title", "")}
五点：{" | ".join(listing.get("bullet_points") or [])}
描述：{listing.get("description", "")}
关键词：{listing.get("search_terms", "")}

评分口径（请严格遵守，避免过度解读）：
- 只评估语言地道性、卖点表达清晰度、信息一致性三项。
- 商品型号名（如 Pro Max、Air、Ultra）是正常命名，不算违规词，不要因此扣分。
- 不要臆造平台规则，也不要评价图片素材（图片由独立流程生成）。

【事实一致性 · 重点】逐条核对 Listing 中的**事实性声明**是否能在「商品参考信息」中找到依据，包括：
配件与赠品（如发光件、替换手型、支架、收纳盒）、数量与件数、材质、尺寸/重量/容量、
适用年龄、认证与检测、性能承诺。凡参考信息中不存在、也无合理推断依据的，即为**编造卖点**，
必须写进 issues；数量被改动（如参考 18cm 写成 15cm）同样算不一致。
语言风格、语法、标点问题不要写进 issues（那是评分维度）。

输出 JSON（comments 与 suggestions 用中文，合计控制在 80 字内）：
{{
  "score": 0-100 的整数,
  "comments": "点评：最大亮点与最大问题",
  "consistency": "信息一致性结论",
  "suggestions": ["建议1", "建议2"],
  "issues": [
    {{"field": "title|bullet_1|description|keywords", "claim": "编造或无依据的说法（原文摘录）", "fix": "如何修正（删除/改为参考信息中的真实表述）"}}
  ]
}}
issues 最多 3 条、claim 控制在 24 字内（无则输出空数组），总输出务必精简以免被截断。"""


async def validate_node(state: dict) -> dict:
    emitter = state["emitter"]
    llm: LlmClient = state["llm"]
    card = state["knowledge_card"]
    listings = state.get("listings") or {}
    plan = state.get("plan") or {}
    units: list[dict] = plan.get("units") or []

    await emitter.step(S_CHECK, "running", "正在执行规则校验与语言质量评分…")

    platforms_out: list[dict] = []
    issues_out: dict[str, list[dict]] = {}
    quality_out: dict[str, dict] = dict(state.get("quality") or {})
    sem = asyncio.Semaphore(CONCURRENCY)

    # 修正轮中只对"内容发生变化的单元"重新评分，其余直接复用上一轮结果。
    # 规则校验是本地计算、成本极低，每轮都跑；LLM 评分才需要按需触发。
    fixed_units = state.get("fixed_langs")
    reused: list[str] = []

    async def run(u: dict):
        key, pk, mk = u["key"], u["pk"], u["market"]
        p = PLATFORMS.get(pk)
        market = MARKET_BY_LANG.get(u["lang_code"])
        if not p or not market:
            return
        listing = listings.get(key) or {}

        issues = check_text(
            listing, p.rules,
            lang_code=u["lang_code"], language=market.language,
        )
        need_score = fixed_units is None or (key in fixed_units)

        if not need_score and key in quality_out:
            quality = quality_out[key]
            reused.append(f"{p.name}×{market.label}")
        else:
            async with sem:
                # 评分是唯一会「把好 Listing 打 0 分」的环节：网关偶发限流/空响应时
                # 重试一次（低温、更短输出），仍失败才记为未评分（failed 标记，前端显示 —）
                msgs = [{"role": "system", "content": _SYSTEM},
                        {"role": "user", "content": _score_prompt(p, market, listing, card)}]
                # 三段尝试：主模型两次（低温、更短输出）→ 兜底模型一次。
                # 主模型网关偶发 200 空/JSON 解析失败时，兜底一次能把
                # 「好好的 Listing 被打 0 分」的概率压到最低。
                fallback_model = state["llm"].settings.llm_text_model_fallback
                attempts = [(None, 0.2), (None, 0.1), (fallback_model, 0.2)]
                last_err = ""
                quality = None
                for model, temp in attempts:
                    try:
                        quality = await llm.chat_json(
                            msgs,
                            model=model,
                            temperature=temp,
                            max_tokens=MAX_TOKENS_SCORE,
                        )
                        break
                    except Exception as e:
                        last_err = str(e)[:80]
                        await asyncio.sleep(1.5)
                if quality is None:
                    quality = {
                        "score": 0,
                        "failed": True,               # 未评分 ≠ 0 分，前端据此显示「—」
                        "comments": f"质量评分失败：{last_err}",
                        "consistency": "未校验",
                        "suggestions": [],
                    }
                    await emitter.log(
                        f"质量评分失败（含兜底模型重试）：{last_err}", level="warn"
                    )

        # 评分模型回报的「编造卖点」清单：升级为 error，让它进入 fix 闭环自动重写，
        # 而不是只扣分、带着虚假卖点直接导出（虚假卖点 = 退货/差评/平台处罚风险）。
        fab = quality.pop("issues", None)
        if isinstance(fab, list):
            for f in fab[:5]:
                if not isinstance(f, dict):
                    continue
                claim = str(f.get("claim") or "").strip()
                if not claim:
                    continue
                issues.append(Issue(
                    "error", str(f.get("field") or "consistency"),
                    f"与商品信息不符（编造卖点）：{claim[:60]}",
                    str(f.get("fix") or "删除该说法，改回商品参考信息中的真实表述"),
                ))

        issue_dicts = [i.to_dict() for i in issues]
        quality_out[key] = quality
        issues_out[key] = issue_dicts

        err_n = sum(1 for i in issue_dicts if i["level"] == "error")
        await emitter.log(
            f"{p.name}×{market.label} 校验完成：{err_n} 项不合规 · 语言质量 {quality.get('score', 0)} 分"
            + ("（复用上轮评分）" if not need_score else "")
        )
        platforms_out.append({
            "key": key,
            "pk": pk,
            "name": p.name,
            "image_size": p.image_size,
            "image_px": p.image_px,
            "file_ext": p.file_ext,
            "flag": market.flag,
            "market_label": market.label,
            "lang_code": u["lang_code"],
            "language": market.language,
            "listing": listing,
            "checks": issue_dicts,
            "quality": quality,
            "rules": {
                "titleMax": p.rules.title_max,
                "bulletMax": p.rules.bullet_max,
                "bulletCount": p.rules.bullet_count,
                "keywordMax": p.rules.keyword_max,
                "descMax": p.rules.desc_max,
                "mainImage": p.rules.main_image,
            },
        })

    await asyncio.gather(*[run(u) for u in units])

    # 保持与用户选择顺序一致（units 本身已按平台×市场有序）
    platforms_out.sort(key=lambda x: next(
        (i for i, u in enumerate(units) if u["key"] == x["key"]), 999))

    # 平均分只统计成功评分的单元；未评分（failed）按 0 分计入会拉低均值误导判断
    scored = [q for q in quality_out.values() if not q.get("failed")]
    avg = round(sum(int(q.get("score") or 0) for q in scored) / len(scored)) if scored else 0
    failed_n = len(quality_out) - len(scored)
    await emitter.emit("platforms", platforms=platforms_out)
    suffix = f" · {failed_n} 项评分失败" if failed_n else ""
    await emitter.step(S_CHECK, "done", f"{len(platforms_out)} 份站点 Listing 校验完成 · 平均 {avg} 分{suffix}")
    return {"platforms": platforms_out, "issues": issues_out, "quality": quality_out}
