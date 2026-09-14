# -*- coding: utf-8 -*-
"""复现质量评分失败：用用户报告的耳机 Listing 实测 deepseek 评分输出。"""
import asyncio, json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.config import get_settings
from app.core.llm import LlmClient, extract_json, LlmError

LISTING = {
    "title": "Wireless Bluetooth 5.0 Earphones in-Ear with 8H Playtime, Type-C Charging, Noise Reducing Mic, Ergonomic Design for Workouts Commuting and Office",
    "bullet_points": [
        "Stable and efficient Bluetooth 5.0 connectivity provides a reliable wireless range up to 33 feet, ensuring seamless pairing with smartphones, tablets, and laptops. The low-power chipset extends battery life while delivering crisp audio with minimal dropouts, perfect for daily use.",
        "Enjoy up to 8 hours of continuous music playback on a single charge, and the compact magnetic charging case delivers an additional 24 hours of power. The USB Type-C port allows for quick recharging, so you can keep your earbuds ready for long commutes, gym sessions, or workdays.",
        "Ergonomically shaped in-ear design with soft silicone ear tips (3 sizes included) ensures a secure and comfortable fit for most ear shapes. The lightweight construction stays in place during running, cycling, or head movements, reducing fatigue even after extended wear.",
        "Built-in high-definition microphone with environmental noise reduction technology captures clear voice calls by minimizing background sounds like wind or traffic. Ideal for hands-free phone calls, virtual meetings, or voice assistant commands in noisy environments.",
        "The magnetic charging case not only protects the earphones when not in use but also provides convenient storage and automatic power management. The sleek matte black finish and LED indicator add a modern touch, while the case itself is compact enough to fit in a pocket or bag.",
    ],
    "description": "Experience wireless freedom with these Bluetooth 5.0 in-ear earphones, designed for everyday convenience and reliable performance. Featuring a stable connection, long battery life, and a comfortable ergonomic fit, they are ideal for music lovers, commuters, and fitness enthusiasts alike. Whether you are on the train, at the gym, or in a virtual meeting, these earphones deliver clear audio and hands-free calling without the hassle of tangled wires.\n\nThe earphones come with a sleek magnetic charging case that extends total playtime to approximately 32 hours, with quick USB Type-C charging for minimal downtime. The in-ear design with multiple silicone ear tips ensures a snug, secure fit, while the built-in microphone effectively reduces ambient noise for clearer conversations. Compact and lightweight, the case slides easily into your pocket or bag, making these earphones a perfect travel companion for daily use.",
    "search_terms": "wireless earbuds bluetooth earphones noise isolating sport earbuds in ear headphones type c charging mic handsfree workout commuting",
}

CARD = json.dumps({
    "product_name": "TWS Wireless Bluetooth 5.0 Earphones",
    "category": "消费电子/耳机",
    "core_selling_points": [
        "蓝牙5.0稳定连接，有效距离约10米（33英尺）",
        "单次充电续航8小时，充电仓额外补充24小时，总计约32小时",
        "USB Type-C 快充接口",
        "人体工学入耳设计，附3种尺寸硅胶耳帽",
        "内置高清麦克风，环境降噪，通话清晰",
        "磁吸充电仓，哑光黑外观，LED 电量指示灯",
    ],
    "specs": ["蓝牙版本=5.0", "续航=单耳8H/整套32H", "充电接口=USB Type-C", "耳帽=3尺寸硅胶", "颜色=哑光黑"],
}, ensure_ascii=False)

SYSTEM = "你是跨境电商 Listing 质量审核专家。只输出严格合法的 JSON 对象。"

def score_prompt():
    return f"""请以Amazon平台标准审核下面这条English Listing，给出语言质量评分。

平台：Amazon
商品参考信息：
{CARD}

Listing 内容：
标题：{LISTING['title']}
五点：{" | ".join(LISTING['bullet_points'])}
描述：{LISTING['description']}
关键词：{LISTING['search_terms']}

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


async def main():
    s = get_settings()
    llm = LlmClient(s)
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": score_prompt()}]
    for i in range(3):
        try:
            raw = await llm.chat(msgs, json_mode=True, temperature=0.2, max_tokens=2000)
            print(f"--- 第{i+1}次: raw len={len(raw)} ---")
            print(repr(raw[:150]))
            try:
                obj = extract_json(raw)
                print(f"解析成功: score={obj.get('score')}")
            except LlmError as e:
                print(f"解析失败: {e}")
        except LlmError as e:
            print(f"--- 第{i+1}次: 调用失败: {e} ---")
        await asyncio.sleep(1)

asyncio.run(main())
