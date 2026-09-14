"""节点：商品信息解析（多模态）

对应方案「模块A：商品信息解析引擎」。
优先携带实拍图进行视觉理解；若模型不接受图片输入，自动降级为纯文本解析。
"""
from __future__ import annotations

from app.agent.emitter import S_PARSE
from app.core.llm import extract_json

_SYSTEM = (
    "你是跨境电商商品分析专家。你的唯一输出是严格合法的 JSON 对象，"
    "不要输出任何解释、注释或 Markdown 代码块。"
)


def _user_text(params: dict) -> str:
    brand = params.get("brand") or ""
    price = params.get("price")
    currency = params.get("currency") or "USD"
    price_line = (
        f"参考售价：{currency} {price}（用户指定，suggested_price 必须使用该值）"
        if price
        else "参考售价：未提供（请基于品类合理估计）"
    )
    brand_line = brand or '未提供（请填 "Generic"，不要从商品名猜测品牌，避免商标侵权风险）'
    return f"""请基于以下商品原始信息，生成"商品知识卡片"。

商品名称（中文）：{params.get("product_name", "")}
商品品类：{params.get("category", "")}
中文描述：{params.get("description", "")}
规格参数：{params.get("specs", "")}
品牌：{brand_line}
{price_line}
目标市场：{", ".join(params.get("market_labels") or [])}

请输出如下结构的 JSON：
{{
  "product_name_zh": "中文商品名",
  "product_name_en": "英文商品名",
  "category": "品类路径，如 3C数码 / 耳机",
  "brand": "品牌英文名",
  "core_selling_points": ["卖点1", "卖点2", "卖点3", "卖点4", "卖点5"],
  "specs": [{{"name": "参数名", "value": "参数值"}}],
  "target_audience": ["人群1", "人群2"],
  "usage_scenarios": ["场景1", "场景2", "场景3"],
  "visual_features": {{"color": "", "material": "", "shape": "", "style": ""}},
  "image_prompt_subject": "英文，80词以内，客观描述该商品的外观、材质、颜色、形态，用于图像生成",
  "package_contents": "包装清单",
  "suggested_price": {{"currency": "{currency}", "value": 39.99}},
  "hs_keywords": ["用于类目推荐的英文关键词"]
}}
要求：
1. core_selling_points 必须 5 条，每条 12-25 字，突出可量化卖点。
2. 用户提供的规格参数必须原样保留，不得改写数值；不足 5 项时可补充品类通用规格（如接口类型、材质），但严禁编造认证、检测标准、获奖信息或量化性能承诺（如 IPX7、99% 抗菌率）。
3. 如提供了商品图片，请结合图片描述外观特征；如无图片，请基于文字合理推断。
4. 全部字段必须填充，不得留空。"""


async def parse_node(state: dict) -> dict:
    emitter = state["emitter"]
    params = state["params"]
    llm = state["llm"]

    await emitter.step(S_PARSE, "running", "正在调用多模态模型解析商品信息…")
    card: dict | None = None
    images = params.get("images") or []

    if images:
        try:
            await emitter.log(f"尝试携带 {len(images)} 张实拍图进行视觉理解…")
            content = [
                {"type": "text", "text": _user_text(params)},
                *[{"type": "image_url", "image_url": {"url": img}} for img in images[:5]],
            ]
            raw = await llm.chat(
                [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": content}],
                temperature=0.3,
                max_tokens=3000,
            )
            card = extract_json(raw)
        except Exception as e:
            await emitter.warn(f"视觉理解不可用（{str(e)[:80]}），已降级为纯文本解析")

    if card is None:
        card = await llm.chat_json(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": _user_text(params)}],
            temperature=0.3,
            max_tokens=3000,
        )

    points = card.get("core_selling_points") or []
    specs = card.get("specs") or []
    if not points:
        card["core_selling_points"] = ["（模型未返回卖点，已保留描述原文）"]

    # 用户显式提供的品牌/售价具有最高优先级，硬覆盖模型输出；
    # 品牌未填时强制 Generic——从商品名猜品牌有商标侵权风险
    card["brand"] = params.get("brand") or "Generic"
    if params.get("price"):
        card["suggested_price"] = {
            "currency": params.get("currency") or "USD",
            "value": params["price"],
        }
    card["price_source"] = "user" if params.get("price") else "model"

    await emitter.emit("card", card=card)
    await emitter.log(f"知识卡片已生成：{len(points)} 个核心卖点 · {len(specs)} 项规格参数")
    await emitter.step(
        S_PARSE, "done",
        f"{card.get('product_name_en') or card.get('product_name_zh')} · {len(points)} 个核心卖点",
    )
    return {"knowledge_card": card}
