"""节点：任务规划（Orchestrator）

对应方案「规划阶段：Orchestrator Agent 接收商品原始信息，分析商品类型
和目标平台，制定生成计划」。LLM 规划失败时自动回退到规则分配，不阻断流程。

任务单元（unit）：一个 (平台, 市场) 对产出一份该站点语言的 Listing。
units 由用户在输入页的「平台 × 市场」矩阵直接指定（platform_markets），
语言 = 市场语言，无需模型再分配。
"""
from __future__ import annotations

from app.agent.emitter import S_PARSE
from app.rules.platforms import MARKET_BY_KEY, PLATFORMS

_SYSTEM = (
    "你是跨境电商上架 Agent 的任务规划器（Orchestrator）。"
    "你的唯一输出是严格合法的 JSON 对象，禁止输出解释或 Markdown 代码块。"
)


def build_units(platform_markets: dict[str, list[str]]) -> list[dict]:
    """把 {平台: [市场]} 展开为任务单元列表。

    unit = {"key": "amazon:us", "pk": "amazon", "market": "us", "lang_code": "en"}
    key 作为 listings/issues/quality/retry 的统一键，保证同平台多市场互不覆盖。
    """
    units: list[dict] = []
    seen: set[str] = set()
    for pk, mks in (platform_markets or {}).items():
        p = PLATFORMS.get(pk)
        if not p:
            continue
        for mk in mks:
            m = MARKET_BY_KEY.get(mk)
            if not m:
                continue
            key = f"{pk}:{mk}"
            if key in seen:
                continue
            seen.add(key)
            units.append({
                "key": key, "pk": pk, "market": mk, "lang_code": m.lang_code,
            })
    return units


def _fallback_units(params: dict) -> list[dict]:
    """兜底：用户没传 platform_markets 时，按平台默认市场各出一个单元"""
    platform_markets = {
        pk: [PLATFORMS[pk].default_market]
        for pk in (params.get("platforms") or []) if pk in PLATFORMS
    }
    return build_units(platform_markets)


def _fallback_plan(params: dict) -> dict:
    units = _fallback_units(params)
    return {
        "units": units,
        "strategy": "规则兜底计划：按平台×市场逐单元生成",
        "source": "rule",
    }


async def plan_node(state: dict) -> dict:
    emitter = state["emitter"]
    params = state["params"]
    await emitter.step(S_PARSE, "running", "Orchestrator 正在分析商品与目标平台…")

    platform_markets = params.get("platform_markets") or {}
    # 任务单元始终由「平台×市场」矩阵展开；用户未给出时才回退到平台默认市场
    units = build_units(platform_markets) or _fallback_units(params)
    plan = _fallback_plan(params)
    plan["units"] = units
    try:
        # 只让模型给一句策略。此前还要求它输出 image_plan 与 category_type，
        # 但全链路没有任何读者（图片清单实际由 images.py 的 MAIN_SPECS/DETAIL_SPECS
        # 决定，且与品类无关），留着会让人误以为出图按品类规划 —— 死字段比没字段更误导。
        user = f"""请为本次上架任务给出执行策略。

商品名称：{params.get("product_name", "")}
商品品类：{params.get("category", "")}
上架单元（平台×市场）：{", ".join(f"{u['pk']}×{u['market']}" for u in plan["units"])}

请输出 JSON：
{{
  "strategy": "60字以内的执行策略说明（中文）"
}}"""
        data = await state["llm"].chat_json(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
            temperature=0.2,
            max_tokens=800,   # 规划输出很短，收紧上限以缩短首屏等待
        )
        plan["strategy"] = str(data.get("strategy") or plan["strategy"])
        plan["source"] = "llm"
        await emitter.log(f"任务规划完成：{plan['strategy']}")
    except Exception as e:  # 规划失败不阻断，使用规则兜底
        await emitter.warn(f"规划模型调用失败，已使用规则兜底计划：{str(e)[:120]}")

    lang_desc = " · ".join(
        f"{PLATFORMS[u['pk']].name}×{u['market']}→{u['lang_code']}"
        for u in plan["units"] if u["pk"] in PLATFORMS
    )
    await emitter.log(f"任务单元（{len(plan['units'])}）：{lang_desc}")
    return {"plan": plan}
