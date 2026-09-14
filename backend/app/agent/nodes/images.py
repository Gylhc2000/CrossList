"""节点：商品主图与详情页生成

对应方案「模块C：商品主图生成引擎 + 模块D：详情页编排引擎」。
调用图像生成模型产出 5 张主图 + 4 张详情页；单张失败自动降级为占位 SVG，
不影响文案与模板文件生成。

用户上传的实拍图会作为「参考图」送进图像模型（图生图），保证出图的商品
外观、颜色、结构与参考图一致；没有实拍图时才退回纯文本描述出图。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import re

from app.agent.emitter import S_IMAGE
from app.core.llm import LlmClient, is_content_risk
from app.services.storage import Storage

CONCURRENCY = 4   # 与 listing 并行执行，适当提高并发缩短出图总时长
SIZE = "1024x1024"
# 参考图：最多用前 2 张（多角度收益有限，但请求体与耗时翻倍），并压到 1024px 内
MAX_REFS = 2
REF_MAX_SIDE = 1024

# (key, 文件名, 展示名, prompt 模板)
MAIN_SPECS: list[tuple[str, str, str, str]] = [
    ("main_1", "main_1_white.png", "白底主图",
     "{base}, on a pure white background (#FFFFFF), centered composition, no text, no watermark, no props"),
    ("main_2", "main_2_lifestyle.png", "场景图",
     "{base}, in a realistic lifestyle scene matching the target audience, warm natural light, shallow depth of field, no text"),
    ("main_3", "main_3_feature1.png", "卖点图 · 卖点1",
     "{base}, hero shot highlighting: {p0}, clean gradient studio background, product infographic style, no text"),
    ("main_4", "main_4_feature2.png", "卖点图 · 卖点2",
     "{base}, hero shot highlighting: {p1}, clean gradient studio background, product infographic style, no text"),
    ("main_5", "main_5_dimension.png", "尺寸图",
     "{base}, dimension and scale reference shot, neutral grey studio background, orthographic view, clear silhouette, no text"),
]

DETAIL_SPECS: list[tuple[str, str, str, str]] = [
    ("detail_1", "detail_1_hook.png", "详情页 · 首图吸引",
     "{base}, bold hero banner image for product detail page, dramatic lighting, clean background, no text"),
    ("detail_2", "detail_2_painpoint.png", "详情页 · 痛点引入",
     "{base}, everyday annoyance solved scene, storytelling composition, warm tones, no text"),
    ("detail_3", "detail_3_features.png", "详情页 · 卖点展开",
     "{base}, exploded view showing structure and key components, white background, clean infographic style, no text"),
    ("detail_4", "detail_4_specs.png", "详情页 · 规格场景",
     "{base}, in-use scenario with accessories and package contents neatly arranged, flat lay, top view, bright clean background, no text"),
]


def placeholder_svg(label: str, sub: str = "") -> bytes:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800" viewBox="0 0 800 800">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#fff3e0"/><stop offset="1" stop-color="#ffd7b0"/>'
        "</linearGradient></defs>"
        '<rect width="800" height="800" fill="url(#g)"/>'
        '<rect x="24" y="24" width="752" height="752" fill="none" stroke="#FF6A00" '
        'stroke-width="4" stroke-dasharray="18 12" rx="16"/>'
        f'<text x="400" y="380" font-size="48" font-family="Microsoft YaHei, sans-serif" '
        f'fill="#FF6A00" text-anchor="middle" font-weight="700">{label}</text>'
        f'<text x="400" y="450" font-size="24" font-family="Microsoft YaHei, sans-serif" '
        f'fill="#a86a2a" text-anchor="middle">{sub or "DEMO PLACEHOLDER"}</text>'
        "</svg>"
    ).encode("utf-8")


_CJK = re.compile(r"[\u4e00-\u9fff]")


def _has_cjk(s: str) -> bool:
    return bool(_CJK.search(s or ""))


def _generic_subject(card: dict) -> str:
    """脱敏后的英文通用主体。

    优先用知识卡片的英文关键词（hs_keywords），但剔除与商品名重合的词
    —— 对 IP 类商品（如 Ultraman）这正是风控命中的根因；
    没有可用关键词时退回品类末级词（中文则退回 "product"）。
    """
    name = (card.get("product_name_en") or card.get("product_name_zh") or "").lower()
    name_tokens = {t for t in re.split(r"[^a-z0-9]+", name) if len(t) > 2}
    picked: list[str] = []
    for kw in card.get("hs_keywords") or []:
        kw = str(kw).strip()
        if not kw or _has_cjk(kw):
            continue
        if any(t in kw.lower() for t in name_tokens):  # 含品牌/IP 名，跳过
            continue
        picked.append(kw)
        if len(picked) == 2:
            break
    if picked:
        return " ".join(picked)
    cat = (card.get("category") or "").replace("｜", "/").replace("|", "/")
    leaf = cat.split("/")[-1].strip() if cat else ""
    return leaf if leaf and not _has_cjk(leaf) else "product"


def _prepare_refs(params: dict) -> list[str]:
    """把用户上传的实拍图整理成可直接送模型的参考图列表。

    - 只取前 MAX_REFS 张（第一张通常是正面主图，信息量最大）
    - data URL 解码后等比压到 REF_MAX_SIDE 内并转 JPEG，避免 base64 撑大请求体
    - 非 data URL（如 http 地址）原样透传；单张解析失败即跳过，不拖垮整个出图
    """
    raw = [u for u in (params.get("images") or []) if isinstance(u, str) and u.strip()]
    out: list[str] = []
    for u in raw[:MAX_REFS]:
        if not u.startswith("data:"):
            out.append(u)
            continue
        try:
            head, b64 = u.split(",", 1)
            data = base64.b64decode(b64)
            from PIL import Image

            img = Image.open(io.BytesIO(data))
            img.load()
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((REF_MAX_SIDE, REF_MAX_SIDE), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=88)
            out.append("data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode())
        except Exception:
            continue
    return out


def _base_variants(
    card: dict, subject: str, look: str, consistency: str, has_ref: bool
) -> list[tuple[str, str, bool]]:
    """返回 [(提示词主体, 说明, 是否带参考图)]，按「保真 -> 脱敏 -> 极简」分级。

    - 有参考图时：前两级都带图（V2 只是把品牌/IP 措辞去掉），
      最后一级才放弃参考图退回纯文生图，作为风控下的兜底。
    - 无参考图时：三级都是纯文生图，与原行为一致。
    图像模型对动漫/品牌 IP 类商品会返回 IPInfringementSuspect 直接拒绝，
    因此逐级去掉 IP 特征词重试。
    """
    cat = _generic_subject(card)
    vf = card.get("visual_features") or {}
    cm = " ".join(str(vf[k]) for k in ("color", "material") if vf.get(k)).strip()
    cm = f" in {cm}" if cm and not _has_cjk(cm) else ""

    # 图生图基准描述：明确要求「照抄参考照里的那件商品」，否则模型容易重新设计
    ref_lock = (
        "the EXACT product shown in the attached reference photo — keep its identical shape, "
        "proportions, colors, materials, surface finish and every visible detail; "
        "do not redesign, restyle or substitute it with a different product"
    )
    v3 = (
        f"a plain generic unbranded {cat}, simple studio product photograph, "
        f"plain light background, centered, no logo, no brand name, no text"
    )
    v3_note = "已极简化重试（仅保留品类，未使用参考图）" if has_ref else "已极简化重试（仅保留品类）"

    if has_ref:
        v1 = (
            f"{ref_lock}.{consistency} "
            f"high quality commercial product photography, sharp detail, "
            f"professional studio lighting, 8k"
        )
        v2 = (
            f"{ref_lock}. a generic unbranded {cat}{cm}, original generic design, "
            f"no logo, no brand name, no trademark, no copyrighted character likeness, no text, "
            f"high quality commercial product photography, professional studio lighting"
        )
        return [
            (v1, "", True),
            (v2, "已脱敏重试（移除品牌/IP 特征词）", True),
            (v3, v3_note, False),
        ]

    v1 = (
        f"{subject},{consistency} "
        f"high quality commercial product photography, sharp detail, professional studio lighting, 8k"
    )
    v2 = (
        f"a generic unbranded {cat}{cm}, original generic design, "
        f"no logo, no brand name, no trademark, no copyrighted character likeness, no text, "
        f"high quality commercial product photography, professional studio lighting"
    )
    return [(v1, "", False), (v2, "已脱敏重试（移除品牌/IP 特征词）", False), (v3, v3_note, False)]


async def image_node(state: dict) -> dict:
    emitter = state["emitter"]
    params = state["params"]
    llm: LlmClient = state["llm"]
    storage: Storage = state["storage"]
    card = state["knowledge_card"]
    job_id = state["job_id"]

    if not params.get("with_images"):
        await emitter.step(S_IMAGE, "skipped", "已跳过图像生成（可在输入页关闭）")
        return {"images": []}

    subject = card.get("image_prompt_subject") or card.get("product_name_en") or "consumer product"
    # 系列一致性锁定：9 张图描述同一件商品，颜色/材质显式来自知识卡片。
    # 措辞保持品类无关（不点名具体部件），任何商品都适用；
    # 小部件（耳塞/按键/缝线等）的漂移由「every component and detail」兜底覆盖。
    vf = card.get("visual_features") or {}
    look = "、".join(
        str(vf[k]) for k in ("color", "material", "style") if vf.get(k)
    )
    consistency = (
        f" SERIES CONSISTENCY: every image shows the exact same product — identical model, "
        f"identical design and identical colors/finish ({look}). "
        f"The overall color scheme and every component, part and visible detail must be "
        f"exactly the same in all images. "
        f"Part of one coherent product photoshoot with consistent lighting and proportions."
        if look
        else " SERIES CONSISTENCY: every image shows the exact same product with identical design, colors, finish and every visible detail, one coherent product photoshoot."
    )
    points = card.get("core_selling_points") or []
    p0 = points[0] if len(points) > 0 else "key feature"
    p1 = points[1] if len(points) > 1 else (points[0] if points else "second feature")
    # 实拍参考图：有则走图生图（出图商品外观与实拍图一致），无则退回文生图
    refs = await asyncio.to_thread(_prepare_refs, params)
    variants = _base_variants(card, subject, look, consistency, has_ref=bool(refs))
    # 任务级固定 seed：同任务 9 张图共用，进一步收窄风格漂移（网关不支持时自动降级）
    seed = int(hashlib.md5(job_id.encode()).hexdigest()[:8], 16) % (2**31)

    specs = MAIN_SPECS + DETAIL_SPECS
    if refs:
        await emitter.step(
            S_IMAGE, "running",
            f"正在以 {len(refs)} 张实拍图为参考生成素材图（{llm.settings.llm_image_model}）…",
        )
        await emitter.log(f"已启用实拍图参考（{len(refs)} 张）：出图将保持与参考图一致的商品外观与配色")
    else:
        await emitter.step(S_IMAGE, "running", f"正在调用 {llm.settings.llm_image_model} 生成素材图…")
        await emitter.log("未上传实拍图，按商品描述文本生成素材图（外观为模型推断）")

    images: list[dict] = []
    lock = asyncio.Lock()
    finished = {"n": 0}
    # 一旦某张图在 V1 命中 IP 风控，其余图直接跳过 V1，省去一轮必然失败的等待
    skip_v1 = {"on": False}
    ip_blocked = {"n": 0}
    sem = asyncio.Semaphore(CONCURRENCY)

    async def run(key: str, filename: str, label: str, tpl: str):
        rel = f"images/{filename}"
        ok, err, note, prompt = False, "", "", ""
        data = b""
        async with lock:
            start = 1 if skip_v1["on"] else 0
        for vi in range(start, len(variants)):
            base_v, note_v, use_ref = variants[vi]
            # 脱敏变体同时替换可能含品牌/IP 词的卖点文案
            fmt = {
                "base": base_v,
                "p0": p0 if vi == 0 else "key structural detail",
                "p1": p1 if vi == 0 else "secondary structural detail",
            }
            prompt = tpl.format(**fmt)
            try:
                data = await llm.generate_image(
                    prompt,
                    size=SIZE,
                    seed=seed,
                    ref_images=refs if (use_ref and refs) else None,
                )
                ok, err, note = True, "", note_v
                break
            except Exception as e:
                err, note = str(e)[:160], note_v
                if is_content_risk(e) and vi < len(variants) - 1:
                    async with lock:
                        skip_v1["on"] = True
                    await asyncio.sleep(0.6)
                    continue
                break
        if not ok:
            data = placeholder_svg(label, "图像生成失败")
            if is_content_risk(err):
                async with lock:
                    ip_blocked["n"] += 1
                err = "疑似 IP/版权内容被图像风控拦截（已自动脱敏重试）"
        storage.save(job_id, rel, data)

        async with lock:
            finished["n"] += 1
            item = {
                "name": key,
                "label": label,
                "path": rel,
                "url": f"/api/jobs/{job_id}/asset?p={rel}",
                "ok": ok,
                "error": err,
                "note": note,
                "prompt": prompt,
                "group": "main" if key.startswith("main") else "detail",
            }
            images.append(item)
            await emitter.emit("image", image=item)
            await emitter.step(S_IMAGE, "running",
                               f"已完成 {finished['n']}/{len(specs)} 张 · {label}"
                               f"{'' if ok else '（降级为占位图）'}")
            if ok and note:
                await emitter.log(f"{label} 生成成功（{note}）", "info")
            else:
                await emitter.log(f"{label} {'生成成功' if ok else '生成失败：' + err}",
                                  "info" if ok else "warn")

    async def guarded(*args):
        async with sem:
            await run(*args)

    await asyncio.gather(*[guarded(*s) for s in specs])

    images.sort(key=lambda x: x["name"])
    ok_count = sum(1 for i in images if i["ok"])
    masked_count = sum(1 for i in images if i["ok"] and i.get("note"))
    await emitter.step(S_IMAGE, "done",
                       f"主图 5 张 + 详情页 4 张 · 成功 {ok_count}/{len(images)}")
    if masked_count:
        await emitter.warn(
            f"{masked_count} 张图在首次生成时被内容风控拦截，已改用脱敏提示词重新生成："
            + (
                "画面仍以实拍图为参考，但不再还原具体品牌/IP 形象，正式上架前请核对细节；"
                if refs
                else "画面保留品类/颜色/材质等通用特征，不再还原具体 IP 形象，正式上架前请替换为实拍图；"
            )
            + "预览页图片角标可查看逐张处理方式"
        )
    if ip_blocked["n"]:
        await emitter.warn(
            f"{ip_blocked['n']} 张图被图像模型内容风控拦截，已逐级降级重试仍失败并落为占位图。"
            + (
                "建议：参考图本身可能是品牌/IP 商品，请改用无 IP 的实拍图后重跑"
                if refs
                else "建议：商品本身为 IP 类（动漫/品牌周边）时改用无 IP 素材，或上传实拍图由模型参考生成"
            )
        )
    elif ok_count == 0:
        await emitter.warn("图像生成接口调用失败，已全部降级为占位图，不影响文案与模板文件生成")
    return {"images": images}
