"""探针：三种"字符预算"措辞对实际产文长度的影响（英语 vs 韩语）。

背景：给提示词加"按语言密度折算"的说明后，韩语长度正常了，但**英语卖点也一起变短**
（Amazon 美国平均 252 → 159 字符）。怀疑是说明段落整体稀释了下限的约束力。
本探针用同一张卡片、同一模型，分别用三种措辞各跑 3 次，比卖点/描述长度。

用法：python _test_prompt_len.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from app.agent.nodes.listing import _SYSTEM, _density, build_listing_prompt  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.rules.platforms import MARKET_BY_KEY, PLATFORMS  # noqa: E402

CARD = {
    "product_name_zh": "真无线降噪蓝牙耳机",
    "product_name_en": "True Wireless Earbuds",
    "category": "3C数码 / 耳机",
    "brand": "Generic",
    "core_selling_points": ["蓝牙5.3稳定连接", "主动降噪", "充电仓400mAh持久续航", "单次续航6小时", "入耳式贴合舒适"],
    "specs": [
        {"name": "充电仓容量", "value": "400mAh"},
        {"name": "单次续航", "value": "6小时"},
        {"name": "蓝牙", "value": "5.3"},
    ],
    "target_audience": ["通勤人群"],
    "usage_scenarios": ["地铁通勤", "办公"],
    "visual_features": {},
    "package_contents": "未提供",
    "suggested_price": {"currency": "USD", "value": 29.99},
    "hs_keywords": ["wireless earbuds"],
}

# 说明段落（当前版本）
NOTE = re.compile(r"说明：上面的字符数已按.*?而不是把字数堆多。", re.S)

# 预算行（当前版本）
BUDGET_CUR = re.compile(r"- 卖点：恰好.*?\n")
BUDGET_OLD = re.compile(r"- 商品描述：不少于.*?\n")


def variant_firm(base: str, rules) -> str:
    """P2：删掉说明段，把下限写回"必须满足的区间"。"""
    s = NOTE.sub("", base)
    return s.replace(
        "3. 严格满足上述字符限制 —— **只有上限是硬约束**（超限必须精简）；下限是信息量要求，不必为凑字数扩写。输出前逐条估算字符数。",
        "3. 严格满足上述字符区间：超限的必须精简；**低于下限说明该条内容还不完整，请补足后重新输出**。输出前逐条估算字符数。",
    )


def variant_two_numbers(base: str, rules, note: bool = False) -> str:
    """P3/P4：恢复"不少于 X，理想 Y~Z"的双数字写法（= 改动前的措辞）。

    note=True 时在预算块末尾补一行**简短**的密度说明（P4）。
    """
    floor = int(rules.bullet_max * 0.18)
    db, _ = _density("en")
    ideal = int(rules.bullet_max * 0.45 * db)
    safe = int(rules.bullet_max * 0.85)
    s = NOTE.sub("", base)
    s = s.replace(
        "3. 严格满足上述字符限制 —— **只有上限是硬约束**（超限必须精简）；下限是信息量要求，不必为凑字数扩写。输出前逐条估算字符数。",
        "3. 严格满足上述字符限制；输出前逐条估算字符数，超限的必须精简后重新输出。",
    )
    s = re.sub(
        r"- 卖点：恰好 (\d+) 条.*?\n",
        f"- 卖点：恰好 5 条，每条不少于 {floor} 字符，理想 {ideal} ~ {safe} 字符（硬上限 {rules.bullet_max}，不要贴着上限写）\n",
        s,
    )
    if note:
        s = s.replace(
            "- 商品描述：不少于",
            "注：以上字符数已按本语言的表达密度折算（韩语/日语等承载同样信息量所需字符数约为英语的一半，属正常）。\n- 商品描述：不少于",
        )
    return s


async def run(client, s, prompt: str, tag: str, i: int) -> tuple:
    body = {
        "model": s.llm_text_model,
        "messages": [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
        "temperature": 0.6,
        "max_tokens": 2500,
        "enable_thinking": False,
    }
    r = await client.post(
        f"{s.llm_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {s.llm_api_key}"},
        json=body,
    )
    d = json.loads(r.json()["choices"][0]["message"]["content"])
    bl = [len(b) for b in d["bullet_points"]]
    print(f"    [{tag}#{i}] 卖点 {bl} 均值 {sum(bl)//len(bl)} | 描述 {len(d['description'])}")
    return sum(bl) / len(bl), len(d["description"])


async def main() -> None:
    s = get_settings()
    market = MARKET_BY_KEY["kr"] if "--ko" in sys.argv else MARKET_BY_KEY["us"]
    rules = PLATFORMS["amazon"].rules
    base = build_listing_prompt(CARD, market, rules)
    ps = {"P3 双数字(不少于X,理想Y~Z)": variant_two_numbers(base, rules),
          "P4 双数字+一行密度注释": variant_two_numbers(base, rules, note=True)}
    print(f"市场={market.label} 语言={market.language} 平台=amazon(bullet_max={rules.bullet_max})\n")
    async with httpx.AsyncClient(timeout=300.0) as c:
        for tag, p in ps.items():
            print(f"  {tag}")
            res = [await run(c, s, p, tag.split()[0], i) for i in range(1, 4)]
            print(f"  → 3 次平均：卖点 {sum(x[0] for x in res)/3:.0f} 字符 | 描述 {sum(x[1] for x in res)/3:.0f}\n")


asyncio.run(main())
