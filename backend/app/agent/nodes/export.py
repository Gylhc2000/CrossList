"""节点：批量上传格式生成与打包

对应方案「模块E：批量上传格式生成」+「交付阶段」。
- Amazon → Flat File（.xlsx，openpyxl，含字段说明 Sheet）
- AliExpress / Shopee / TikTok → 批量上传 CSV（pandas，utf-8-sig）
- 图片按平台命名规范输出到对应文件夹
- 每平台输出 Listing 文案 + 质量报告
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime

from app.agent.emitter import S_PACK
from app.rules.platforms import (
    ALIEXPRESS_COLUMNS,
    AMAZON_COLUMNS,
    PLATFORMS,
    SHOPEE_COLUMNS,
    TIKTOK_COLUMNS,
)
from app.services.exporters.amazon import build_amazon_flatfile
from app.services.exporters.csv_exporter import build_csv
from app.services.imagefit import fit_image_bytes
from app.services.storage import Storage

IMAGE_FILES = [
    "main_1_white.png", "main_2_lifestyle.png", "main_3_feature1.png",
    "main_4_feature2.png", "main_5_dimension.png",
]
DETAIL_FILES = [
    "detail_1_hook.png", "detail_2_painpoint.png",
    "detail_3_features.png", "detail_4_specs.png",
]


def slug(text: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", str(text or "product")).strip("_")
    return s[:40] or "product"


# ---------------- 包裹重量/尺寸：从规格参数提取（商务字段不猜值） ----------------

_DIM_PAT = re.compile(
    r"(\d+(?:\.\d+)?)\s*[×x*X*]\s*(\d+(?:\.\d+)?)\s*[×x*X*]\s*(\d+(?:\.\d+)?)"
    r"\s*(mm|毫米|cm|厘米|m|in|inch|英寸|英寸)?",
    re.I,
)
_LEN_F = {"mm": 0.1, "毫米": 0.1, "m": 100, "in": 2.54, "inch": 2.54, "英寸": 2.54}


def _extract_dims(specs: list[dict]) -> tuple[float, float, float] | None:
    """从规格值中找「长×宽×高」，统一转为 cm；找不到返回 None。"""
    for s in specs:
        m = _DIM_PAT.search(str(s.get("value", "")))
        if not m:
            continue
        l, w, h = (float(m.group(i)) for i in (1, 2, 3))
        f = _LEN_F.get((m.group(4) or "cm").lower(), 1)
        return (round(l * f, 1), round(w * f, 1), round(h * f, 1))
    return None


def _extract_weight_kg(specs: list[dict]) -> float | None:
    """从规格名含「重量/净重/毛重/weight」的项提取重量，统一转为 kg；找不到返回 None。"""
    for s in specs:
        name = str(s.get("name", ""))
        if not re.search(r"重量|净重|毛重|weight", name, re.I):
            continue
        m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|千克|公斤|g|克|lb|磅|oz|盎司)?", str(s.get("value", "")), re.I)
        if not m:
            continue
        n = float(m.group(1))
        u = (m.group(2) or "").lower()
        if u in ("kg", "千克", "公斤"):
            return round(n, 3)
        if u in ("g", "克"):
            return round(n / 1000, 4)
        if u in ("lb", "磅"):
            return round(n * 0.4536, 3)
        if u in ("oz", "盎司"):
            return round(n * 0.02835, 3)
        # 无单位：中文规格表默认按克处理
        return round(n / 1000, 4)
    return None


def _pkg_fields(specs: list[dict]) -> dict:
    """返回 Amazon 模板的包裹字段；未提取到的留空（宁缺勿猜）。"""
    dims = _extract_dims(specs)
    weight = _extract_weight_kg(specs)
    return {
        "package_length": dims[0] if dims else "",
        "package_width": dims[1] if dims else "",
        "package_height": dims[2] if dims else "",
        "package_weight": weight if weight else "",
        "package_length_unit": "CM" if dims else "",
        "package_weight_unit": "KG" if weight else "",
    }


def _build_row(pk: str, card: dict, listing: dict) -> dict:
    """按平台模板字段组装一行数据"""
    bullets = listing.get("bullet_points") or []
    price = card.get("suggested_price") or {}
    name_en = card.get("product_name_en") or card.get("product_name_zh") or "product"
    visual = card.get("visual_features") or {}
    specs = card.get("specs") or []
    keywords = card.get("hs_keywords") or []

    img = lambda f: f"images/{f}"  # noqa: E731

    if pk == "amazon":
        return {
            "feed_product_type": "consumer-electronics",
            "item_sku": f"CL-{slug(name_en).upper()}-001",
            "brand_name": card.get("brand") or (str(name_en).split(" ")[0] if name_en else "Generic"),
            "item_name": listing.get("title", ""),
            "product_description": listing.get("description", ""),
            "bullet_point1": bullets[0] if len(bullets) > 0 else "",
            "bullet_point2": bullets[1] if len(bullets) > 1 else "",
            "bullet_point3": bullets[2] if len(bullets) > 2 else "",
            "bullet_point4": bullets[3] if len(bullets) > 3 else "",
            "bullet_point5": bullets[4] if len(bullets) > 4 else "",
            "generic_keywords": listing.get("search_terms", ""),
            "main_image_url": img("main_1_white.png"),
            "other_image_url1": img("main_2_lifestyle.png"),
            "other_image_url2": img("main_3_feature1.png"),
            "other_image_url3": img("main_4_feature2.png"),
            "other_image_url4": img("main_5_dimension.png"),
            "other_image_url5": img("detail_1_hook.png"),
            "item_type": card.get("category", ""),
            "color_name": visual.get("color", ""),
            "size_name": specs[0]["value"] if specs else "",
            "part_number": f"PN-{slug(name_en).upper()}",
            "manufacturer": card.get("brand") or "CrossList",
            "product_id": "", "product_id_type": "GTIN",             "condition_type": "New",
            "standard_price": price.get("value", 39.99),
            "currency": price.get("currency", "USD"),
            "quantity": 500, "fulfillment_latency": 3,
            **_pkg_fields(specs),
            "country_of_origin": "CN",
            "warranty_description": "12 months manufacturer warranty",
            "is_adult_product": "No", "target_gender": "Unisex",
            "recommended_browse_nodes": " ".join(keywords),
        }

    if pk == "aliexpress":
        pkg = _pkg_fields(specs)
        return {
            "Product Title": listing.get("title", ""),
            "Product Description": listing.get("description", ""),
            "Category ID": "/".join(keywords),
            "Product Images": ";".join(img(f) for f in IMAGE_FILES),
            "Product Attributes": ";".join(f"{s.get('name')}:{s.get('value')}" for s in specs),
            "Key Selling Points": ";".join(card.get("core_selling_points") or []),
            "Search Keywords": listing.get("search_terms", ""),
            "SKU Code": f"SKU-{slug(name_en).upper()}",
            "SKU Price": price.get("value", 39.99),
            "SKU Stock": 500,
            "Currency": price.get("currency", "USD"),
            "Shipping From": "CN",
            "Package Length(cm)": pkg["package_length"],
            "Package Width(cm)": pkg["package_width"],
            "Package Height(cm)": pkg["package_height"],
            "Package Weight(kg)": pkg["package_weight"],
            "Brand Name": card.get("brand") or "Generic",
            "Warranty": "12 months",
            "Language": listing.get("meta", {}).get("language", ""),
            "Country/Region": listing.get("meta", {}).get("label", ""),
        }

    if pk == "shopee":
        pkg = _pkg_fields(specs)
        weight_g = round(pkg["package_weight"] * 1000) if pkg["package_weight"] != "" else ""
        return {
            "商品名称": listing.get("title", ""),
            "商品描述": listing.get("description", ""),
            "类目ID": "/".join(keywords),
            "商品价格": price.get("value", 39.99),
            "商品库存": 500,
            "商品图片1": img("main_1_white.png"),
            "商品图片2": img("main_2_lifestyle.png"),
            "商品图片3": img("main_3_feature1.png"),
            "商品图片4": img("main_4_feature2.png"),
            "商品图片5": img("main_5_dimension.png"),
            "商品规格": "；".join(f"{s.get('name')}：{s.get('value')}" for s in specs),
            "搜索关键词": listing.get("search_terms", ""),
            "商品重量(g)": weight_g,
            "包裹长(cm)": pkg["package_length"],
            "包裹宽(cm)": pkg["package_width"],
            "包裹高(cm)": pkg["package_height"],
            "品牌": card.get("brand") or "Generic",
            "保修期": "12个月",
            "语言": listing.get("meta", {}).get("language", ""),
            "站点": listing.get("meta", {}).get("label", ""),
        }

    pkg = _pkg_fields(specs)
    return {  # tiktok
        "Product Name": listing.get("title", ""),
        "Product Description": listing.get("description", ""),
        "Category": "/".join(keywords),
        "Product Images": ";".join(img(f) for f in IMAGE_FILES),
        "Price": price.get("value", 39.99),
        "Currency": price.get("currency", "USD"),
        "Stock": 500,
        "SKU ID": f"SKU-{slug(name_en).upper()}",
        "SKU Name": name_en,
        "Search Keywords": listing.get("search_terms", ""),
        "Weight(kg)": pkg["package_weight"],
        "Package Length(cm)": pkg["package_length"],
        "Package Width(cm)": pkg["package_width"],
        "Package Height(cm)": pkg["package_height"],
        "Brand": card.get("brand") or "Generic",
        "Warranty": "12 months",
        "Language": listing.get("meta", {}).get("language", ""),
        "Region": listing.get("meta", {}).get("label", ""),
    }


def _listing_txt(p, card: dict, listing: dict) -> str:
    return "\n".join([
        f"平台：{p.name}（{listing.get('meta', {}).get('label', '')} · {listing.get('meta', {}).get('language', '')}）",
        f"商品：{card.get('product_name_zh', '')} / {card.get('product_name_en', '')}",
        "",
        "【Title】", listing.get("title", ""), "",
        "【Bullet Points】",
        *[f"{i+1}. {b}" for i, b in enumerate(listing.get("bullet_points") or [])],
        "",
        "【Description】", listing.get("description", ""), "",
        "【Search Terms】", listing.get("search_terms", ""), "",
    ])


def _report_md(p, card: dict, listing: dict, checks: list[dict], quality: dict, models: dict) -> str:
    lines = [
        f"# 质量校验报告 · {p.name}（{listing.get('meta', {}).get('label', '')} {listing.get('meta', {}).get('language', '')}）",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"使用模型：{models.get('text', '')}" + (f" + {models.get('image')}" if models.get("image") else ""),
        "",
        "## 规则校验",
    ]
    if checks:
        lines += [
            f"- [{'不合规' if c['level'] == 'error' else '警告'}] {c['field']}：{c['msg']}"
            + (f"（建议：{c['fix']}）" if c.get("fix") else "")
            for c in checks
        ]
    else:
        lines.append("- 全部通过，无告警")
    lines += [
        "",
        "## 语言质量",
        f"- 评分：{quality.get('score', 0)}/100",
        f"- 点评：{quality.get('comments', '—')}",
        f"- 信息一致性：{quality.get('consistency', '—')}",
        *[f"- 建议：{s}" for s in (quality.get("suggestions") or [])],
        "",
        "## 平台规范",
        f"- 标题上限：{p.rules.title_max} 字符（当前 {len(listing.get('title', ''))}）",
        f"- 主图要求：{p.rules.main_image}（目标尺寸 {p.image_size}，导出图已自动适配至该尺寸）",
    ]

    # 商务字段来源标注：让「模型估计/待补」在上传前可见
    price = card.get("suggested_price") or {}
    if card.get("price_source") == "model":
        lines.append(
            f"- 价格：{price.get('currency', 'USD')} {price.get('value', '')}（模型估计值，建议核对成本与竞品后再上传）"
        )
    pkg = _pkg_fields(card.get("specs") or [])
    missing = [
        name
        for name, ok in (
            ("包裹重量", bool(pkg["package_weight"])),
            ("包裹尺寸（长×宽×高）", bool(pkg["package_length"])),
        )
        if not ok
    ]
    if missing:
        lines.append(
            f"- 待补字段：{'、'.join(missing)}未能从规格参数提取，模板中已留空，请上传前补填（影响运费/FBA 费用计算）"
        )
    if card.get("brand") == "Generic":
        lines.append("- 品牌：Generic（未提供品牌，已安全兜底；如为自有品牌请补填后重新生成）")
    return "\n".join(lines)


async def export_node(state: dict) -> dict:
    emitter = state["emitter"]
    params = state["params"]
    storage: Storage = state["storage"]
    job_id = state["job_id"]
    card = state["knowledge_card"]
    platforms = state.get("platforms") or []

    await emitter.step(S_PACK, "running", "正在按平台批量上传模板生成文件并打包…")

    artifacts: dict[str, list[dict]] = {}
    models = {
        "text": state["llm"].settings.llm_text_model,
        "image": state["llm"].settings.llm_image_model if params.get("with_images") else None,
    }
    images = state.get("images") or []
    # 脱敏/占位图的数量只用于前端提示，说明一律在网页端呈现，不写入下载包
    masked_n = sum(1 for i in images if i.get("ok") and i.get("note"))
    failed_n = sum(1 for i in images if not i.get("ok"))

    for plat in platforms:
        pk = plat.get("pk") or plat["key"]          # 平台 key（目录）
        unit_key = plat["key"]                       # 任务单元键 "pk:mk"
        p = PLATFORMS.get(pk)
        if not p:
            continue
        listing = plat.get("listing") or {}
        mk = unit_key.split(":", 1)[1] if ":" in unit_key else ""
        mk_tag = f"_{mk.upper()}" if mk else ""      # 同平台多市场时区分文件
        files: list[dict] = []

        # 1) 图片按平台命名规范输出（缩放/白底补边至平台 image_px 要求，线程池并行）
        if params.get("with_images"):
            srcs = [
                (f, storage.read(job_id, f"images/{f}"))
                for f in IMAGE_FILES + DETAIL_FILES
                if storage.exists(job_id, f"images/{f}")
            ]
            outs = await asyncio.gather(
                *[asyncio.to_thread(fit_image_bytes, raw, p.image_px) for _, raw in srcs]
            )
            for (f, raw), fitted in zip(srcs, outs):
                storage.save(job_id, f"{pk}/images/{f}", fitted if fitted is not None else raw)
            img_rels = [
                f"{pk}/images/{f}"
                for f in IMAGE_FILES + DETAIL_FILES
                if storage.exists(job_id, f"{pk}/images/{f}")
            ]
            if img_rels:
                img_size = sum(len(storage.read(job_id, rel)) for rel in img_rels)
                files.append(
                    {
                        "name": f"images/（{len(img_rels)} 张）",
                        "type": "images",
                        "size": img_size,
                    }
                )

        # 2) Listing 文案
        lang = (plat.get("lang_code") or "en").upper()
        txt = _listing_txt(p, card, listing).encode("utf-8")
        storage.save(job_id, f"{pk}/Listing_{lang}{mk_tag}.txt", txt)
        files.append({"name": f"Listing_{lang}{mk_tag}.txt", "type": "listing", "size": len(txt)})

        # 3) 批量上传模板
        row = _build_row(pk, card, listing)
        if pk == "amazon":
            tpl_name = f"Amazon_FlatFile{mk_tag}.xlsx"
            data = build_amazon_flatfile(row, AMAZON_COLUMNS)
        elif pk == "aliexpress":
            tpl_name = f"AliExpress_BulkUpload{mk_tag}.csv"
            data = build_csv(ALIEXPRESS_COLUMNS, row)
        elif pk == "shopee":
            tpl_name = f"Shopee_BulkImport{mk_tag}.csv"
            data = build_csv(SHOPEE_COLUMNS, row)
        else:
            tpl_name = f"TikTokShop_BulkUpload{mk_tag}.csv"
            data = build_csv(TIKTOK_COLUMNS, row)
        storage.save(job_id, f"{pk}/{tpl_name}", data)
        files.append({"name": tpl_name, "type": "template", "size": len(data)})

        # 4) 质量报告
        md = _report_md(p, card, listing, plat.get("checks") or [], plat.get("quality") or {}, models)
        md_b = md.encode("utf-8")
        storage.save(job_id, f"{pk}/质量报告{mk_tag}.md", md_b)
        files.append({"name": f"质量报告{mk_tag}.md", "type": "report", "size": len(md_b)})

        artifacts[unit_key] = files
        await emitter.log(f"{p.name}×{mk} 素材包已生成：{', '.join(f['name'] for f in files)}")

    report = {
        "generated_at": datetime.now().isoformat(),
        "models": models,
        "platform_count": len({p.get("pk") for p in platforms}),
        "unit_count": len(platforms),
        "language_scores": [
            {
                "platform": x["name"],
                "market": x.get("market_label", ""),
                "language": x["language"],
                "score": int(x.get("quality", {}).get("score") or 0),
                "failed": bool(x.get("quality", {}).get("failed")),
            }
            for x in platforms
        ],
        "error_count": sum(1 for x in platforms for c in (x.get("checks") or []) if c["level"] == "error"),
        "warn_count": sum(1 for x in platforms for c in (x.get("checks") or []) if c["level"] == "warn"),
        "image_ok": sum(1 for i in images if i.get("ok")),
        "image_total": len(images),
        "image_masked": masked_n,
        "image_failed": failed_n,
        "retry_rounds": state.get("retry") or {},
        "plan_source": (state.get("plan") or {}).get("source", "rule"),
    }

    await emitter.step(S_PACK, "done", f"{len(platforms)} 份站点素材包已就绪")
    return {"artifacts": artifacts, "report": report}
