"""节点：多语言 Listing 生成（按 平台×市场 任务单元）

对应方案「模块B：多语言Listing生成引擎」。
知识卡片 → Prompt 模板 → LLM 草稿 → 规则校验（在 validate 节点）→ 不合规自动修正（fix 节点）。
本节点只负责"生成"，修正逻辑在 fix 节点，形成 LangGraph 条件回边。
"""
from __future__ import annotations

import asyncio

from app.agent.emitter import S_LISTING
from app.core.llm import LlmClient
from app.rules.platforms import BANNED_WORDS, MARKET_BY_KEY, PLATFORMS

_SYSTEM = (
    "你是服务 Amazon / AliExpress / Shopee 等跨境平台的资深 Listing 文案专家。"
    "你的唯一输出是严格合法的 JSON 对象，禁止输出解释或 Markdown 代码块。"
)

CONCURRENCY = 3

# 输出 token 预算：Listing 实际约 800-1400 token，给足 1600 足以，
# 上限过大会显著拖慢推理速度（输出 token 是本流程最大耗时项）
MAX_TOKENS_LISTING = 1600


def build_listing_prompt(card: dict, market, rules: PlatformRules, fix_hint: str = "") -> str:
    # 留出安全余量，避免模型压线生成导致校验失败返工。
    # 卖点下限刻意设低（0.45）：下限越高越会诱导模型往长里写，而 LLM 对字符数
    # 估算天生偏乐观，贴着上限写几乎必然超限（此前 0.6 下限导致 5 条全部超限）。
    title_budget = int(rules.title_max * 0.85)
    bullet_budget = int(rules.bullet_max * 0.45)
    bullet_safe_max = int(rules.bullet_max * 0.85)
    kw_budget = int(rules.keyword_max * 0.85)
    banned = "、".join(f'"{w}"' for w in BANNED_WORDS)

    base = f"""请基于商品知识卡片，为目标市场撰写一条完整的电商 Listing。

目标市场：{market.label}（{market.language}，语言代码 {market.lang_code}）

【字符预算 · 必须严格遵守，禁止压线】
- 标题：{title_budget} ~ {rules.title_max} 字符之间（硬上限 {rules.title_max}，宁可偏短也不要超限）
- 卖点：恰好 {rules.bullet_count} 条，每条 {bullet_budget} ~ {bullet_safe_max} 字符（硬上限 {rules.bullet_max}，宁可偏短，切忌写满）
- 搜索关键词：不超过 {kw_budget} 字符（硬上限 {rules.keyword_max}）
- 商品描述：不超过 {rules.desc_max} 字符，2-4 段

【禁用词清单 · 以下词汇绝对禁止出现在任何位置（含大小写与词形变化）】
{banned}
说明：这些词属于医疗声称、绝对化用语或违规促销表述。请改用中性、可验证的客观描述。

商品知识卡片：
{_dump(card)}

请输出如下 JSON：
{{
  "title": "用{market.language}书写的商品标题，含核心关键词与关键规格，客观陈述不加夸大",
  "bullet_points": ["要点1", "要点2"],
  "description": "用{market.language}书写的商品描述，2-4 段，可读性强",
  "search_terms": "用{market.language}书写的搜索关键词，空格分隔，8-12 个，不重复标题已有词"
}}
要求：
1. 所有文案必须使用{market.language}母语级表达，符合当地消费者阅读习惯，禁止机翻腔。
2. 逐字检查上面的禁用词清单，确保标题、卖点、描述、关键词中一个都不出现。
3. 严格满足上述字符限制；输出前逐条估算字符数，超限的必须精简后重新输出。
4. 语言纯度：整条 Listing 必须用{market.language}书写，禁止把整句、整段写成其他语言（例如{market.language}文案里夹带中文词或整句外文）。但下列情况使用英文原文是**必要且正确**的，必须原样保留、严禁翻译成{market.language}：(a) 品牌名、系列名、角色名等专有名词；(b) 型号与材质/技术缩写，如 PVC、ABS、LED、USB-C、RGB、IP68；(c) 计量单位与数字，如 18cm、0.5kg、5V。除上述情形外，不要为了凑字数堆砌英文。
5. 知识卡片中 brand 为 "Generic" 表示未提供品牌：文案任何位置禁止出现 "Generic" 或任何品牌名；若 brand 为其他具体品牌，则标题应以该品牌名开头。
6. 搜索关键词只能使用与**本商品**直接相关的通用品类词与属性词，禁止堆砌其他 IP、动漫角色或竞品品牌词蹭流量（如假面骑士、高达、宝可梦、LEGO 等），避免商标侵权与误导。
7. 事实底线：所有配件、赠品、数量、件数、材质、尺寸、重量、适用年龄、认证与性能描述，必须能在知识卡片中找到依据或由其合理推断；严禁凭常识或角色设定编造（例如给手办加“发光配件”“替换手型 N 对”“共 N 件套”“通过 XX 认证”等）。知识卡片没有写到的，就当作不存在，改写其他真实卖点；数量与尺寸必须与卡片一致，不得自行改成另一个数值。"""
    if fix_hint:
        base += f"\n\n【本次为修正重生成，必须解决以下问题】\n{fix_hint}"
    return base


def _truncate(s: str, limit: int) -> str:
    """长度兜底截断：超限时在词/句边界断开，避免把单词或句子截成半截。

    LLM 对字符数估算普遍偏乐观，仅靠提示词无法保证永不超限，
    这里作为最后一道保险，确保导出的素材一定满足平台硬上限。
    """
    s = (s or "").strip()
    if len(s) <= limit:
        return s
    cut = s[:limit]
    # 回退到最后一个分隔符保证语义完整；回退幅度不超过 40%，否则宁可硬截
    best = -1
    for sep in (" ", "，", "。", "、", "；", ",", ".", ";"):
        idx = cut.rfind(sep)
        if idx > best:
            best = idx
    if best >= int(limit * 0.6):
        return cut[:best].rstrip(" ,.;，。、；")
    return cut.rstrip()


def _dump(card: dict) -> str:
    import json

    return json.dumps(card, ensure_ascii=False, indent=2)


async def generate_one(
    llm: LlmClient,
    card: dict,
    market,
    rules: PlatformRules,
    fix_hint: str = "",
    temperature: float = 0.6,
    model: str | None = None,
) -> dict:
    data = await llm.chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": build_listing_prompt(card, market, rules, fix_hint)},
        ],
        temperature=temperature,
        max_tokens=MAX_TOKENS_LISTING,
        model=model,
    )
    bullets = data.get("bullet_points") or []
    if isinstance(bullets, str):
        bullets = [bullets]

    # 长度兜底：即便提示词已给出安全区间，模型仍可能估不准字符数而超限。
    # 这里统一收敛到平台硬上限，避免无谓的 fix 返工轮次。
    title = _truncate(str(data.get("title") or ""), rules.title_max)
    bullets = [_truncate(str(b), rules.bullet_max) for b in bullets]
    search_terms = _truncate(
        str(data.get("search_terms") or data.get("keywords") or ""), rules.keyword_max
    )
    description = _truncate(str(data.get("description") or ""), rules.desc_max)

    return {
        "title": title,
        "bullet_points": bullets,
        "description": description,
        "search_terms": search_terms,
        "meta": {
            "key": market.key, "label": market.label,
            "flag": market.flag, "language": market.language,
            "lang_code": market.lang_code,
        },
    }


async def listing_node(state: dict) -> dict:
    emitter = state["emitter"]
    params = state["params"]
    llm: LlmClient = state["llm"]
    card = state["knowledge_card"]
    plan = state.get("plan") or {}

    units = plan.get("units") or []
    if not units:
        raise RuntimeError("任务单元为空：未确定任何平台×市场组合")

    await emitter.step(S_LISTING, "running", f"正在生成 {len(units)} 个平台×市场单元的 Listing…")

    listings: dict[str, dict] = {}   # unit_key("pk:mk") -> listing
    errors: list[str] = []
    sem = asyncio.Semaphore(CONCURRENCY)

    def _meta(u: dict, market) -> dict:
        return {
            "key": u["key"], "pk": u["pk"], "market": u["market"],
            "label": market.label, "flag": market.flag,
            "language": market.language, "lang_code": market.lang_code,
        }

    async def run(u: dict):
        async with sem:
            market = MARKET_BY_KEY[u["market"]]
            rules = PLATFORMS[u["pk"]].rules
            tag = f"{PLATFORMS[u['pk']].name}×{market.label}"
            try:
                listing = await generate_one(llm, card, market, rules)
                listing["meta"] = _meta(u, market)
                await emitter.emit("listing", market=listing["meta"], listing=listing)
                await emitter.log(
                    f"{tag} Listing 已生成（标题 {len(listing['title'])} 字符）"
                )
                return u["key"], listing
            except Exception as e1:
                # 网关偶发空响应/超时：立即重试一次（低温），多数一次即成功
                try:
                    listing = await generate_one(
                        llm, card, market, rules, temperature=0.4
                    )
                    listing["meta"] = _meta(u, market)
                    await emitter.emit("listing", market=listing["meta"], listing=listing)
                    await emitter.log(f"{tag} Listing 首次生成失败后重试成功")
                    return u["key"], listing
                except Exception as e2:
                    errors.append(f"{tag}：{str(e2)[:120]}（首次：{str(e1)[:80]}）")
                    # 关键：失败也要占位（空 listing），否则 fix 闭环会跳过该单元，
                    # 空白模板将直接流到预览/导出。validate 报「标题为空」error 后由 fix 重写。
                    return u["key"], {
                        "title": "", "bullet_points": [], "description": "",
                        "search_terms": "", "meta": _meta(u, market),
                    }

    results = await asyncio.gather(*[run(u) for u in units])
    for key, listing in results:
        listings[key] = listing

    failed = [k for k, v in listings.items() if not v.get("title")]
    for e in errors:
        await emitter.warn(f"Listing 生成异常 → {e}")

    await emitter.step(
        S_LISTING, "done",
        " · ".join(f"{v['meta']['flag']}{v['meta']['language']}" for v in listings.values())
        + (f"（{len(failed)} 个待修正）" if failed else ""),
    )
    await emitter.emit("listings", listings=listings)
    return {"listings": listings}
