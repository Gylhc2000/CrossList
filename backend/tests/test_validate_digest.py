"""评分模型的事实基准必须完整：截断会让真实声明被误判成编造。

复刻的是一次真实回归：知识卡片里有 IPX5、CE/FCC/RoHS，但基准只传前 8 条 specs，
排在前面的耳机参数以外的那些对评分模型不可见 → 判「编造」→ 升级 error →
修正闭环把真卖点从 Listing 里删掉。后来放宽到 40 条仍与函数自己的 docstring
「全量传入」矛盾，长尺码表、多参数商品照样踩。上游已有 SPECS_MAX=4000 /
DESC_MAX=1200 兜长度，这里不该再二次截。
"""
from app.agent.nodes.validate import _card_digest


def _card(specs: list[dict], description: str) -> dict:
    return {
        "product_name_en": "Thing",
        "category": "家居 / 收纳",
        "core_selling_points": ["a"],
        "specs": specs,
        "package_contents": "主体 ×1",
        "description": description,
    }


def test_every_spec_reaches_the_scoring_baseline():
    specs = [{"name": f"参数{i}", "value": f"值{i}"} for i in range(45)]
    # 排在第 41 位之后的参数同样必须是可见的事实（旧实现截到 40 条）
    tail = specs[-1]["name"]
    assert tail in _card_digest(_card(specs, "d"))


def test_long_description_is_not_cut_mid_sentence():
    # 上游 DESC_MAX=1200，基准应把整段描述交给评分模型（旧实现截到 300 字）
    text = "触" * 1200
    digest = _card_digest(_card([{"name": "尺寸", "value": "30cm"}], text))
    assert text in digest
