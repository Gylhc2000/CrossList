"""节点：不合规自动修正

对应方案「不合规项自动生成修正指令，回调对应模块重新生成」。
在 LangGraph 中由 validate 的条件边触发，修正完成后回到 validate 重新校验，
形成闭环（最多 MAX_RETRY 轮，避免死循环）。

任务单元为 (平台, 市场) 对，键为 "pk:mk"，与 listings/issues 的键一致。
"""
from __future__ import annotations

from app.agent.emitter import S_CHECK
from app.agent.nodes.listing import generate_one
from app.core.llm import LlmClient
from app.rules.platforms import MARKET_BY_KEY, PLATFORMS

MAX_RETRY = 2


def pending_fix_targets(state: dict) -> list[tuple[str, str, dict, list[dict]]]:
    """返回仍需修正的 (unit_key, platform_key, unit, error_issues)"""
    plan = state.get("plan") or {}
    issues = state.get("issues") or {}
    retry = state.get("retry") or {}
    listings = state.get("listings") or {}

    targets: list[tuple[str, str, dict, list[dict]]] = []
    for u in plan.get("units") or []:
        key = u["key"]
        if key not in listings:
            continue
        errs = [i for i in (issues.get(key) or []) if i.get("level") == "error"]
        if errs and int(retry.get(key, 0)) < MAX_RETRY:
            targets.append((key, u["pk"], u, errs))
    return targets


async def fix_node(state: dict) -> dict:
    emitter = state["emitter"]
    llm: LlmClient = state["llm"]
    card = state["knowledge_card"]
    listings = dict(state.get("listings") or {})
    retry = dict(state.get("retry") or {})

    targets = pending_fix_targets(state)
    if not targets:
        return {}

    round_no = max(int(retry.get(t[0], 0)) for t in targets) + 1
    await emitter.step(S_CHECK, "running", f"检测到不合规项，第 {round_no} 轮自动修正…")

    fixed: list[str] = []
    for key, pk, u, errs in targets:
        p = PLATFORMS.get(pk)
        market = MARKET_BY_KEY.get(u["market"])
        if not p or not market:
            continue
        hint = "\n".join(f"- [{i['field']}] {i['msg']}。修正建议：{i.get('fix', '')}" for i in errs)
        try:
            new_listing = await generate_one(
                llm, card, market, p.rules, fix_hint=hint, temperature=0.4
            )
            new_listing["meta"] = {
                "key": key, "pk": pk, "market": u["market"],
                "label": market.label, "flag": market.flag,
                "language": market.language, "lang_code": u["lang_code"],
            }
            listings[key] = new_listing
            retry[key] = int(retry.get(key, 0)) + 1
            fixed.append(key)
            await emitter.emit("listing", market=new_listing["meta"], listing=new_listing)
            await emitter.log(f"{p.name}×{market.label} Listing 第 {retry[key]} 轮修正完成")
        except Exception as e:
            await emitter.warn(f"{p.name}×{market.label} 自动修正失败：{str(e)[:120]}")

    # fixed_langs 语义已升级为"被修正的单元键"，告知 validate 只需对这些单元重新评分
    return {"listings": listings, "retry": retry, "fixed_langs": fixed}
