"""节点：多语言 Listing 生成（按 平台×市场 任务单元）

对应方案「模块B：多语言Listing生成引擎」。
知识卡片 → Prompt 模板 → LLM 草稿 → 规则校验（在 validate 节点）→ 不合规自动修正（fix 节点）。
本节点只负责"生成"，修正逻辑在 fix 节点，形成 LangGraph 条件回边。
"""
from __future__ import annotations

import asyncio

from app.agent.emitter import S_LISTING
from app.core.llm import LlmClient, LlmError
from app.rules.platforms import BANNED_WORDS, MARKET_BY_KEY, PLATFORMS

_SYSTEM = (
    "你是服务 Amazon / AliExpress / Shopee 等跨境平台的资深 Listing 文案专家。"
    "你的唯一输出是严格合法的 JSON 对象，禁止输出解释或 Markdown 代码块。"
)

CONCURRENCY = 3

# 输出 token 预算。
#
# ⚠️ 2026-09-15 更正：空 Listing 的根因**不是预算不够，而是思维链**。
# 本网关的 deepseek-v4-flash 默认开启 thinking，思考会吃光整个 max_tokens：
# 实测（韩语 × 真实提示词，各 4/3 次）4000 → 0/4 全空、每次 40s；
# 8000 → 0/3 全空、每次 80s。**调大预算只会让每次失败更慢**，不会变好。
# 正确做法是请求里带 enable_thinking=false（见 Settings.llm_disable_thinking）——
# 关掉后同一 prompt 3/3~2/2 成功、单次仅 7s，正文约 600~1000 token。
# 所以这里的预算按「正文长度 + 合理余量」给即可，不需要给思维链留空间。
MAX_TOKENS_LISTING = 2500

# 篇幅地板：低于它就判定"内容被写薄了"，触发一次补写重试。
#
# ⚠️ 2026-09-15 实测结论：**地板必须压在"模型自然长度"之下，否则有害**。
# 曾用 bullets≥0.45×bullet_max、desc≥0.3×desc_max（AliExpress 达 135/1200 字符）：
# 韩语站点天然到不了（实测每条 ~100 字符、描述 ~330~650），于是每站都触发补写，
# 而**补写重试 3/3 全部引入编造**（방수防水、이어팁耳塞、실리콘硅胶、케이스 포함含收纳盒）
# —— 正是「编造卖点」的来源，反把合规性搞坏（触发 fix 轮，耗时 68s→96s）。
#
# 第二版（0.18 / 0.12+cap300）仍不够安全：实测 Amazon 韩国卖点最低 92 字符 vs 地板 90，
# **余量只剩 2%** —— 同一站点下次写得稍紧一点就会踩线触发补写，而补写正是编造的来源。
# 所以再降一档，只作**安全网**拦"空稿/残稿"（真正的空字段由 generate_one 的完整性兜底
# 直接抛错，见那里的 raise，不依赖这个地板）。当前取值与实测自然长度的余量：
#   bullets 地板 Amazon 60 / AliExpress 36 / Shopee 24 / TikTok 30（韩语实测最低 53 → +47%）
#   desc    地板 各平台统一 200（韩语实测最低 473 → +137%）
# 长度驱动交给提示词里**按语言密度折算**的下限（见 LANG_CHAR_DENSITY）。
BULLET_MIN_RATIO = 0.12
DESC_MIN_RATIO = 0.10
DESC_FLOOR_CAP = 200

# 提示词里"每条卖点不少于 N 字符"的 N（会再乘语言密度系数）。
# 与上面的校验地板是**两个不同的东西**：这个是给模型看的锚点（驱动长度），
# 上面那个是代码里的判定（拦残稿）。实测下限锚点写的越具体，产文越长（见 build_listing_prompt 注释）。
BULLET_PROMPT_MIN_RATIO = 0.18


def _desc_floor(rules) -> int:
    """补写重试的触发地板（安全网，与语言无关，压在实测自然长度之下）。"""
    return min(int(rules.desc_max * DESC_MIN_RATIO), DESC_FLOOR_CAP)


# 字符密度系数：同样信息量在某语言下的字符数 ÷ 英文的字符数。
#
# 2026-09-15 两轮实测：
#   a) 受控互译（同一段英文描述，要求逐句忠实翻译、信息量不增不减）：
#      韩 0.50 / 日 0.48 / 葡 1.16 / 西 1.17
#   b) 模型独立生成（同一商品 6 站点任务）：韩语描述为英文的 0.52，
#      卖点更低（0.35~0.38 —— 韩文一个分句能塞进更多语义）
# 系数**只缩放提示词里的字符数下限，不缩放上限**（平台硬约束是绝对的）。
# 起因：提示词曾对韩语喊"每条卖点 225~425 字符"（按英文自然长度标定），
# 而韩语自然长度只有 ~100 字符 —— 目标本身就在要求它凑字数，而凑字数的终点是编造
# （实测 3/3 补写全部引入编造）。按密度折算后，提示词的目标落回各语言的自然区间。
LANG_CHAR_DENSITY: dict[str, tuple[float, float]] = {
    # lang_code: (卖点系数, 描述系数)
    "ko": (0.4, 0.5),
    "ja": (0.4, 0.5),
    "en": (1.0, 1.0),
    "de": (1.0, 1.0),
    "pt": (1.15, 1.15),
    "es": (1.15, 1.15),
}


def _density(lang_code: str) -> tuple[float, float]:
    """该语言的 (卖点, 描述) 字符密度系数；未知语言按英文处理（不缩放）。"""
    return LANG_CHAR_DENSITY.get((lang_code or "").lower(), (1.0, 1.0))


def build_listing_prompt(card: dict, market, rules: PlatformRules, fix_hint: str = "") -> str:
    # 留出安全余量，避免模型压线生成导致校验失败返工。
    # 下限按语言密度折算，上限不折算（平台硬约束）。
    #
    # ⚠️ 2026-09-15 措辞 A/B（英语 Amazon，各 3 次，见 backend/_test_prompt_len.py）：
    #   P1 只给区间"字符数 225 ~ 425"          → 卖点 150 / 描述 1179
    #   P2 只给区间 + "低于下限需补足"的语气    → 卖点 149 / 描述 1346（无改善）
    #   P3 "不少于 90 字符，理想 225 ~ 425"     → 卖点 191 / 描述 1368
    #   P4 P3 + 一行密度注释                    → 卖点 208 / 描述 1431
    # 结论：**"不少于 X + 理想 Y~Z"的双数字写法显著优于单区间**（+40%），
    # 而一行密度注释不影响长度、可以保留。单区间写法实测会把英语卖点也压到 150。
    d_bullet, d_desc = _density(getattr(market, "lang_code", ""))
    title_budget = int(rules.title_max * 0.85)
    bullet_floor = int(rules.bullet_max * BULLET_PROMPT_MIN_RATIO * d_bullet)
    bullet_ideal = int(rules.bullet_max * 0.45 * d_bullet)
    bullet_safe_max = int(rules.bullet_max * 0.85)
    kw_budget = int(rules.keyword_max * 0.85)
    desc_ideal = int(min(int(rules.desc_max * 0.3), 700) * d_desc)
    banned = "、".join(f'"{w}"' for w in BANNED_WORDS)

    # 密度说明只在需要折算时出现（英语/德语不折算，说了反而多余）
    density_note = (
        "\n注：以上字符数已按" + market.language + f"的表达密度折算 —— 同样的信息量，"
        f"{market.language}的字符数约为英语的 {round(d_bullet * 100)}%，这是该语言的正常现象。"
        if abs(d_bullet - 1.0) > 0.01
        else ""
    )

    base = f"""请基于商品知识卡片，为目标市场撰写一条完整的电商 Listing。

目标市场：{market.label}（{market.language}，语言代码 {market.lang_code}）

【字符预算】
- 标题：{title_budget} ~ {rules.title_max} 字符之间（硬上限 {rules.title_max}，宁可偏短也不要超限）
- 卖点：恰好 {rules.bullet_count} 条，每条不少于 {bullet_floor} 字符，理想 {bullet_ideal} ~ {bullet_safe_max} 字符（硬上限 {rules.bullet_max}，不要贴着上限写）
- 搜索关键词：不超过 {kw_budget} 字符（硬上限 {rules.keyword_max}）
- 商品描述：不少于 {desc_ideal} 字符，2-4 段（平台硬上限 {rules.desc_max}）{density_note}
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
7. 事实底线：**只有下面三类表述算"有依据"** —— (a) 知识卡片中直接写到的；(b) 对卡片数值做纯算术或单位换算（如 6 小时/次 × 4 次 = 24 小时、400mAh = 0.4Ah）；(c) 卡片参数的同义改写。除此之外的一切具体信息 —— 配件、赠品、件数、材质、尺寸/重量/容量、**接口/端口类型（USB-C、Type-C、Micro-USB 等）、充电方式与充电次数、防水防尘等级**、适用年龄、认证与检测标准、性能承诺 —— 只要卡片里没有，就当作不存在：不得直接写，也不得用"通常""一般""支持""可"等措辞变相引入（例如卡片只给了"充电仓容量 400mAh"，写"USB-C 快充""IPX5 防水""充电仓可多次充电"都属编造）。数量与尺寸必须与卡片一致，不得自行改成另一个数值。
8. 篇幅：请把上面的下限写足 —— 按本语言的自然表达把每个卖点写成 1~2 句完整句子、把描述写成 2~4 段。要写得更充实时，**只允许**展开既有参数带来的好处、使用场景、目标人群与使用体验。**第 7 条优先级高于本条**：绝不允许为了凑字数新增接口/端口、配件、赠品、认证、等级或数值 —— 如果既有信息确实支撑不到下限，写到能写到的最充实程度即可，**宁可写短也不许编造**。"""
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

    # 完整性兜底：命中 max_tokens 被截断时，接口可能只回「标题」这一段合法 JSON，
    # 而 extract_json 的截断补括号修复会把它当成合法对象救回来 ——
    # 直接放行就会把没有卖点/描述的残次 Listing 导出到素材包。
    # 这里显式判为失败，交给上层重试 / fix 闭环重写。
    _bullets = data.get("bullet_points")
    if isinstance(_bullets, str):
        _bullets = [_bullets]
    if (
        not str(data.get("title") or "").strip()
        or not (_bullets or [])
        or not str(data.get("description") or "").strip()
    ):
        raise LlmError("Listing 输出不完整（标题/卖点/描述有缺失，疑似被 max_tokens 截断）")

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


def _thin_parts(listing: dict, rules) -> list[str]:
    """找出"写得太薄"的部分（返回人话描述，空列表=合格）。

    **只作安全网**：拦的是"空稿/残稿"（生成被截断、只写了两三个词），不是"没写够字数"。
    因此地板刻意压在实测自然长度之下 —— 尤其韩语（字符密度只有英文一半）：
    地板若贴近其自然长度，就会频繁误触发补写，而**补写重试实测 3/3 会引入编造**。
    """
    thin: list[str] = []
    desc = listing.get("description") or ""
    desc_floor = _desc_floor(rules)
    if len(desc) < desc_floor:
        thin.append(f"商品描述只有 {len(desc)} 字符，至少需要 {desc_floor}")
    bullets = listing.get("bullet_points") or []
    bfloor = int(rules.bullet_max * BULLET_MIN_RATIO)
    short = [str(i + 1) for i, b in enumerate(bullets) if len(b) < bfloor]
    if short:
        thin.append(f"第 {'、'.join(short)} 条卖点不足 {bfloor} 字符")
    return thin


def _content_len(listing: dict) -> int:
    """正文总长度，用于在「原稿 vs 补写稿」之间挑更充实的那份。"""
    return len(listing.get("description") or "") + sum(
        len(b) for b in (listing.get("bullet_points") or [])
    )


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
                # 内容写薄了就补写一次（关掉思维链后单次只要 7 秒，值得花）。
                # 两份都保留比较，只在补写稿更充实且确实更长时才替换 —— 保证不会更差。
                thin = _thin_parts(listing, rules)
                if thin:
                    hint = (
                        "上次输出内容偏短，请在既有信息允许的范围内把内容写完整："
                        + "；".join(thin)
                        + "。注意衡量标准是信息量而非字符数 —— 按本语言的母语习惯书写即可"
                        "（韩语等语言用更少字符承载同样信息量是正常的）。"
                        "充实内容只允许展开既有参数带来的好处、使用场景、人群与使用体验；"
                        "严禁新增接口/端口、配件、赠品、认证、等级或数值等卡片里没有的具体信息。"
                        "若既有信息确实不足以支撑，写到最充实的程度即可 —— 宁可写短也不许编造。"
                    )
                    try:
                        retry = await generate_one(
                            llm, card, market, rules, fix_hint=hint, temperature=0.5
                        )
                        # 补写稿只有在「不再偏薄」或「正文确实更长」时才替换，保证不会更差
                        if not _thin_parts(retry, rules) or _content_len(retry) > _content_len(listing):
                            before = _content_len(listing)
                            listing = retry
                            await emitter.log(
                                f"{tag} 文案偏短，已自动补写（正文 {before} → {_content_len(listing)} 字符）"
                            )
                    except Exception as e:  # 补写失败不影响主流程，沿用原稿
                        await emitter.log(f"{tag} 补写未成功，沿用原稿：{str(e)[:80]}", "info")
                listing["meta"] = _meta(u, market)
                await emitter.emit("listing", market=listing["meta"], listing=listing)
                await emitter.log(
                    f"{tag} Listing 已生成（标题 {len(listing['title'])} 字符）"
                )
                return u["key"], listing
            except Exception as e1:
                # 网关偶发空响应/超时：立即重试一次（低温），多数一次即成功。
                # 先把失败亮出来：重试链（chat 内部还有多轮退避）可能持续数分钟，
                # 静默重试会让前端看起来像"卡死"
                await emitter.log(f"{tag} 首次生成失败，正在重试：{str(e1)[:80]}", "warn")
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
