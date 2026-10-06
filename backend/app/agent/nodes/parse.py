"""节点：商品信息解析（多模态）

对应方案「模块A：商品信息解析引擎」。
优先携带实拍图进行视觉理解；若模型不接受图片输入，自动降级为纯文本解析。

注意：带图解析**必须用真正支持看图的模型**（`LLM_VISION_MODEL`，默认 qwen3.6-flash）。
实测 deepseek-v4-flash 在本网关会返回 200 但对图片内容全答 unknown/none ——
既不报错也读不到图，知识卡片的颜色/材质等外观字段会退化成凭空猜测，
再被图像提示词引用，反过来把实拍图的外观压掉。
"""
from __future__ import annotations

import re

from app.agent.emitter import S_PARSE
from app.core.llm import extract_json

_SYSTEM = (
    "你是跨境电商商品分析专家。你的唯一输出是严格合法的 JSON 对象，"
    "不要输出任何解释、注释或 Markdown 代码块。"
)

# 「没读到」的各种占位回答，用来识别"图片被模型/网关静默忽略"
_BLANK = {"", "unknown", "none", "n/a", "na", "null", "未知", "无", "无法确定", "无法判断"}


def _appearance_unreadable(card: dict) -> bool:
    """visual_features 全部为空/unknown ⇒ 大概率没真的看到图。"""
    vf = card.get("visual_features")
    if not isinstance(vf, dict):
        return True
    vals = [str(vf.get(k) or "").strip().lower() for k in ("color", "material", "shape", "style")]
    return all(v in _BLANK for v in vals)


_SPEC_LINE = re.compile(r"^([^：:]{1,12})\s*[：:]\s*(.+)$")


def _parse_user_specs(text: str) -> list[tuple[str, str]]:
    """把用户「参数名：参数值」逐行格式解析为键值对；格式不符返回空列表。

    用于在 LLM 解析后**硬覆盖** card.specs：LLM 对数值有"顺手规范化"的
    坏习惯（实测 4.5g→4.2g、24h→40h、-25dB→-35dB，提示词里的
    「原样照抄」约束挡不住），规格是全链路的事实源头，必须代码保证。
    """
    pairs: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip().lstrip("-•·* ").strip()
        if not line:
            continue
        m = _SPEC_LINE.match(line)
        if m:
            pairs.append((m.group(1).strip(), m.group(2).strip()))
    return pairs


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
  "package_contents": "包装清单；用户未提供时填 \\"未提供\\"",
  "suggested_price": {{"currency": "{currency}", "value": 39.99}},
  "hs_keywords": ["用于类目推荐的英文关键词"]
}}
（specs / package_contents 无用户依据时分别给 [] 与 "未提供"）
要求：
1. core_selling_points 恰好 5 条，每条 12-25 字。写成"品类通性"级别的表述即可（如"连接稳定、久戴不累"），**不要为了显得具体而编造数值、配件或认证**。
2. 【事实字段 · 严禁编造】specs 与 package_contents 只能来源于用户输入：
   - 用户给了几项就写几项，参数名、数值、单位必须原样照抄，不得改写、不得换算、不得四舍五入；
   - 不足 5 项就少写：specs 直接给空数组 []，package_contents 填 "未提供"。**绝对不要用品类常识补上"参考尺寸""AAA 电池""随机配件""用户手册"这类内容**；
   - 参数名与字段类型**完全以用户原文为准**：用户写了哪些维度就出哪些维度，任何品类都一样 —— 净含量、配料表、保质期、尺码表、面料成分、适用年龄与「电池容量、接口类型」是同类条目，不必凑齐某一组固定字段，也不要因为用户没写就删掉他写了的；
   - 反过来，用户没写到的维度一律当作不存在：不要因为「这类商品通常都会标XX」就新增条目。宁缺勿编。
3. 【推断字段 · 可合理推断】target_audience、usage_scenarios、visual_features、image_prompt_subject 可基于品类合理推断（如"通勤人群""3C 数码"），但**不得包含具体数值、配件清单或认证信息**。
4. 如提供了商品图片，请结合图片描述 visual_features 的外观特征；如无图片，可基于文字推断外观，但仍不得编造具体尺寸与重量。
5. 除第 2 条的事实约束外，其余字段尽量填充完整；确实无从判断的填 "未提供"，不要硬凑。"""


async def parse_node(state: dict) -> dict:
    emitter = state["emitter"]
    params = state["params"]
    llm = state["llm"]

    await emitter.step(S_PARSE, "running", "正在调用多模态模型解析商品信息…")
    card: dict | None = None
    saw_image = False          # 图片是否真的被解析成功（用于事后判断外观是否可信）
    images = params.get("images") or []

    if images:
        vision_model = llm.settings.llm_vision_model or llm.settings.llm_text_model
        try:
            await emitter.log(f"携带 {len(images)} 张实拍图进行视觉理解（{vision_model}）…")
            content = [
                {"type": "text", "text": _user_text(params)},
                *[{"type": "image_url", "image_url": {"url": img}} for img in images[:5]],
            ]
            raw = await llm.chat(
                [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": content}],
                temperature=0.3,
                max_tokens=6000,   # 思维链 token 共享此预算，留余量防思考吃光（见 listing.py 注释）
                model=vision_model,
            )
            card = extract_json(raw)
            if _appearance_unreadable(card):
                # 典型症状：模型/网关接受了图片却不看图，外观字段全是 unknown/none。
                # 卡片其余字段（卖点/规格）仍可用，但外观**不可信** —— 必须清空，
                # 否则会被图像提示词当成事实引用，反过来把实拍图的颜色压掉。
                await emitter.warn(
                    f"{vision_model} 未能读出实拍图的外观特征（返回 unknown/none）。"
                    f"已丢弃卡片中的颜色等外观字段，素材图改为完全以实拍图为准；"
                    f"Listing 文案的外观描述请人工核对。"
                )
                card["visual_features"] = {}
                card["image_prompt_subject"] = ""
            else:
                saw_image = True
        except Exception as e:
            await emitter.warn(f"视觉理解不可用（{str(e)[:80]}），已降级为纯文本解析")

    if card is None:
        card = await llm.chat_json(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": _user_text(params)}],
            temperature=0.3,
            max_tokens=6000,   # 同上：思维链共享预算，防思考吃光
        )

    # 规格「原样照抄」的硬保障：本地解析用户原文覆盖模型输出（理由见
    # _parse_user_specs 注释）。用户没写规格或格式全不符时，保留模型输出
    user_specs = _parse_user_specs(params.get("specs") or "")
    if user_specs:
        card["specs"] = [{"name": n, "value": v} for n, v in user_specs]
    # 原始描述透传进卡片：卡片 schema 不含描述，触控操作等真实事实会在
    # 解析蒸馏时丢失，listing 只能凭常识重写——碰巧写对也会被评分判「编造」
    if params.get("description"):
        card["description"] = params["description"]

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
    appearance = "实拍图" if saw_image else ("描述文本推断" if not images else "未读到（已丢弃外观字段）")
    await emitter.log(
        f"知识卡片已生成：{len(points)} 个核心卖点 · {len(specs)} 项规格参数 · 外观来源：{appearance}"
    )
    await emitter.step(
        S_PARSE, "done",
        f"{card.get('product_name_en') or card.get('product_name_zh')} · {len(points)} 个核心卖点",
    )
    return {"knowledge_card": card}
