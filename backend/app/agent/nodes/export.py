"""节点：交付物生成与打包

对应方案「模块E：批量上传格式生成」+「交付阶段」。每个平台目录内含：
- Amazon → Flat File（.xlsx，openpyxl，含字段说明 Sheet）
- AliExpress / Shopee / TikTok → 批量上传 CSV（pandas，utf-8-sig）
- 上架对照表（.xlsx）：一字段一行，供在网页后台逐个表单上架时直接复制
- 图片按平台命名规范输出到 images/；Amazon 额外产出按 SKU 命名的图片 zip
  （后台 Catalog·Images·Upload images 收的那种，不需要图片 URL）
- 每平台输出 Listing 文案 + 质量报告
"""
from __future__ import annotations

import asyncio
import io
import re
import zipfile
from datetime import datetime

from app.agent.emitter import S_PACK
from app.rules.platforms import (
    ALIEXPRESS_COLUMNS,
    AMAZON_COLUMNS,
    AMAZON_FIELD_NOTES,
    PLATFORMS,
    SHOPEE_COLUMNS,
    TIKTOK_COLUMNS,
    market_supported,
    utf8_len,
)
from app.services.exporters.amazon import build_amazon_flatfile
from app.services.exporters.csv_exporter import build_csv
from app.services.exporters.worksheet import build_worksheet
from app.services.imagefit import fit_image_bytes, to_jpeg_bytes
from app.services.storage import Storage

IMAGE_FILES = [
    "main_1_white.png", "main_2_lifestyle.png", "main_3_feature1.png",
    "main_4_feature2.png", "main_5_dimension.png",
]
DETAIL_FILES = [
    "detail_1_hook.png", "detail_2_painpoint.png",
    "detail_3_features.png", "detail_4_specs.png",
]
# 图片在包内的固定顺序：主图 5 张 + 详情图 4 张 = 9 张，正好铺满 Amazon 的图位
ALL_IMAGE_FILES = IMAGE_FILES + DETAIL_FILES

# Amazon 图片位编码：主图 MAIN，其余 PT01..PT08（卖家后台的 zip 上传按此识别槽位）
AMZ_SLOTS = ["MAIN"] + [f"PT{i:02d}" for i in range(1, 9)]


def slug(text: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", str(text or "product")).strip("_")
    return s[:40] or "product"


def _name_en(card: dict) -> str:
    return card.get("product_name_en") or card.get("product_name_zh") or "product"


def make_sku(name_en: str, market_key: str) -> str:
    """item_sku 的唯一出处。

    同平台多市场时后缀不同，否则两行 SKU 相同会在店铺里互相覆盖。
    Amazon 的免 URL 图片包还把 SKU 写进文件名（`<SKU>.MAIN.jpg`）来关联商品：
    平台要求文件名里的 SKU 与模板里的**逐字符一致**，所以模板和图片包必须调用
    同一个函数，两处各拼一遍迟早会拼歪。
    """
    mk = str(market_key or "").upper()
    return f"CL-{slug(name_en).upper()}" + (f"-{mk}" if mk else "")


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


# ---------------- 只有卖家才有的信息：一律留空，汇总进「上传前必填」清单 ----------------
# 这里曾经是硬编码猜测值（库存 500、保修 12 个月、原产地 CN、品类 consumer-electronics）。
# 猜错不会让导入报错，而是把那行假数据直接写进卖家店铺 —— 虚假保修声明甚至构成法律承诺。
# 这与文案侧「宁缺勿编」是同一条纪律，此前只是没推广到商务字段。
# 值留空即代表「我们填不了」，报告里逐条列出，卖家填完再上传。
MANUAL_FIELDS: dict[str, dict[str, str]] = {
    "amazon": {
        "feed_product_type": "商品类型是平台枚举值，决定整张表哪些列必填，只能取自后台下载的模板",
        "product_id": "UPC/EAN/GTIN 由 GS1 签发，无法生成；无码需走 GTIN 豁免申请",
        "quantity": "库存数量",
        "country_of_origin": "原产国影响清关与关税，须按实物申报",
        "warranty_description": "保修是对消费者的法律承诺，只能由卖家自行确定",
        "fulfillment_latency": "发货时效，取决于卖家自己的履约能力",
        "target_gender": "适用性别需与实物一致，不作默认值以免错放筛选条件",
        "size_name": "平台尺码须落在该类目允许值内，按实物填写",
        "recommended_browse_nodes": "类目节点是平台签发的数字 ID，不能由关键词拼串代替",
        "main_image_url": "图片列留空即可：包内 Amazon_图片包_*.zip 里的图已按 <SKU>.MAIN.jpg、"
                          "<SKU>.PT01…PT08.jpg 命名，在后台 Catalog·Images·Upload images 上传该 zip "
                          "就能挂上本商品，不需要图片 URL；只有走「模板填 URL」那条路才需要公网地址",
    },
    "aliexpress": {
        "Category ID": "类目 ID 是平台数字 ID，须从后台类目树取；关键词拼串无法解析",
        "SKU Stock": "库存数量",
        "Product Images": "先把包内 images/ 的图上传到平台「图片银行/媒体中心」，取回图片 URL 或图片 ID 填入本列；"
                          "本地路径平台读不到",
        "Warranty": "保修条款由卖家自行确定",
        "Shipping From": "发货地决定运费时效与物流模板，须与实际一致",
        "Brand Name": "品牌须命中平台品牌候选值，未授权品牌会被拦",
    },
    "shopee": {
        "类目ID": "类目 ID 是平台签发的数字 ID，且属性列随类目动态生成，须用后台下载的模板",
        "商品库存": "库存数量",
        "商品图片1": "先把包内 images/ 的图上传到平台「媒体空间」，取回图片 URL 填入图片列；"
                     "本地路径平台读不到",
        "保修期": "保修条款由卖家自行确定",
        "品牌": "Shopee 品牌取品牌编号（无品牌为 No Brand），非自由文本",
    },
    "tiktok": {
        "Category": "类目为平台数字 ID，模板按所选类目生成，属性列随之变化",
        "Stock": "库存数量",
        "Product Images": "先把包内 images/ 的图上传到平台「素材库」，取回 URL 或 image_id 填入本列；"
                          "本地路径不被接受",
        "Warranty": "保修条款由卖家自行确定",
        "Brand": "品牌须先入品牌池/完成授权，未备案品牌会被拒",
    },
}


def _is_placeholder(val) -> bool:
    """该列是不是"我们其实填不了"：留空，或填的是包内相对路径。

    图片列必须保留相对路径，否则卖家看不出哪张图对应哪一列；
    但它在平台侧同样不可用，所以和留空一样要进待补清单。
    """
    s = str(val or "").strip()
    return s == "" or s.startswith("images/")


def _manual_for(pk: str, row: dict) -> list[tuple[str, str]]:
    """列出该模板中我们填不了、必须卖家补的列（仅报真正留空/占位的那些）。"""
    return [
        (col, why)
        for col, why in MANUAL_FIELDS.get(pk, {}).items()
        if col in row and _is_placeholder(row.get(col))
    ]


# 各平台模板里的图片列：对照表把它们从「字段」区块剔出去，单独按槽位说明，
# 因为卖家对图片要做的动作是"传文件"，不是"复制这一格的文字"。
IMAGE_COLUMNS: dict[str, tuple[str, ...]] = {
    "amazon": ("main_image_url", "other_image_url1", "other_image_url2",
               "other_image_url3", "other_image_url4", "other_image_url5"),
    "aliexpress": ("Product Images",),
    "shopee": tuple(f"商品图片{i}" for i in range(1, 6)),
    "tiktok": ("Product Images",),
}

# 各平台模板列集合与文件名：对照表和批量模板必须用同一份列序，
# 否则卖家在对照表里看到的字段名会和 CSV/xlsx 里的表头对不上。
TEMPLATES: dict[str, tuple[list[str], str]] = {
    "amazon": (AMAZON_COLUMNS, "Amazon_FlatFile{}.xlsx"),
    "aliexpress": (ALIEXPRESS_COLUMNS, "AliExpress_BulkUpload{}.csv"),
    "shopee": (SHOPEE_COLUMNS, "Shopee_BulkImport{}.csv"),
    "tiktok": (TIKTOK_COLUMNS, "TikTokShop_BulkUpload{}.csv"),
}

# 字段 → 用哪条平台规则计量。没登记的列只按内容长度展示。
_FIELD_KIND: dict[str, dict[str, str]] = {
    "amazon": {"item_name": "title", "product_description": "desc",
               "generic_keywords": "kw",
               **{f"bullet_point{i}": "bullet" for i in range(1, 6)}},
    "aliexpress": {"Product Title": "title", "Product Description": "desc",
                   "Search Keywords": "kw"},
    "shopee": {"商品名称": "title", "商品描述": "desc", "搜索关键词": "kw"},
    "tiktok": {"Product Name": "title", "Product Description": "desc",
               "Search Keywords": "kw"},
}


def _amazon_image_pack(images: dict[str, bytes], sku: str) -> list[tuple[str, str, str, bytes]]:
    """把适配好的图转成 `<SKU>.<图位>.jpg`，返回 (文件名, 图位, 源图, 字节) 列表。

    这是四条平台路径里唯一不需要卖家先有图床/媒体库的批量入口：后台
    Catalog·Images·Upload images 收一个 zip，靠文件名同时识别"归属哪个商品"
    （SKU，须与模板 item_sku 逐字符一致）和"放第几图位"（MAIN / PT01…PT08）。
    9 张图正好铺满 1 主图 + 8 辅图；缺图（生成失败/降级占位）时对应图位直接不出现，
    对照表据此列出实际有的槽位。

    纯 CPU（PIL 转码），调用方放进线程池。
    """
    out: list[tuple[str, str, str, bytes]] = []
    for slot, fname in zip(AMZ_SLOTS, ALL_IMAGE_FILES):
        raw = images.get(fname)
        if raw is None:
            continue
        jpg = to_jpeg_bytes(raw)
        if jpg:
            out.append((f"{sku}.{slot}.jpg", slot, fname, jpg))
    return out


def _zip_flat(files: dict[str, bytes]) -> bytes:
    """把内存里的文件压成 zip，文件名即键，全部落在 zip 根目录。

    Amazon 的图片上传工具只认根目录里的 `<SKU>.图位.jpg`：多一层文件夹就识别不到，
    所以这里用 writestr 而不是把目录整个塞进去。JPEG 已经压缩过，用 ZIP_STORED
    再 deflat 一遍只省不到 2%，白烧 CPU。
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for name, blob in files.items():
            z.writestr(name, blob)
    return buf.getvalue()


def _measure(value: str, kind: str, rules) -> str:
    """对照表的计量列：字符还是字节、上限多少，直接标在值旁边，
    免得卖家把整段复制到后台才发现超长。"""
    if kind == "title":
        return f"{len(value)} / {rules.title_max} 字符"
    if kind == "bullet":
        return f"{len(value)} / {rules.bullet_max} 字符"
    if kind == "desc":
        return f"{len(value)} 字符（建议 ≤{rules.desc_max}）"
    if kind == "kw":
        if rules.keyword_max_bytes:
            return f"{utf8_len(value)} / {rules.keyword_max_bytes} 字节"
        return f"{len(value)} / {rules.keyword_max} 字符"
    return f"{len(value)} 字符" if value else "—"


def _worksheet_inputs(pk: str, p, columns: list[str], row: dict, card: dict,
                      listing: dict, sku: str, has_images: bool,
                      pack_name: str, pack_slots: list[tuple[str, str]]) -> tuple[str, list[tuple[str, str]], list]:
    """组装「上架对照表」的表头信息与区块行（不含样式，样式在 worksheet.py）。

    pack_slots 为图片包实际存在的 (图位, 源图) 对，缺图时行数随之减少。
    """
    title = f"{p.name} 上架对照表 · {listing.get('meta', {}).get('label', '')}"
    meta: list[tuple[str, str]] = [
        ("商品", f"{card.get('product_name_zh', '')} / {card.get('product_name_en', '')}"),
        ("SKU", sku),
        ("语言", f"{listing.get('meta', {}).get('language', '')}"
                 f"（{listing.get('meta', {}).get('lang_code', '')}）"),
        ("主图规格", f"{p.image_size} · 包内图已按此尺寸适配"),
        ("规则数值核对时间", p.rules.asof or "未核对"),
    ]
    if pack_name:
        meta.append(("图片上传方式",
                     f"{pack_name} 可直接上传：zip 根目录就是 {len(pack_slots)} 张按 SKU 命名好的 jpg，"
                     f"后台 Catalog·Images·Upload images 收的就是这种包，无需图片 URL"))

    manual_cols = {c for c, _ in _manual_for(pk, row)}
    kinds = _FIELD_KIND.get(pk, {})
    notes = AMAZON_FIELD_NOTES if pk == "amazon" else {}

    text_rows: list[list[str]] = []
    for col in columns:
        if col in IMAGE_COLUMNS.get(pk, ()):
            continue
        value = str(row.get(col) or "")
        if value:
            status = "可直接粘贴"
        elif col in manual_cols:
            status = "需卖家补填"
        else:
            status = "可留空"
        note = MANUAL_FIELDS.get(pk, {}).get(col) or notes.get(col) or ""
        text_rows.append([
            col, value, status,
            _measure(value, kinds.get(col, ""), p.rules) if value else "—",
            note,
        ])

    img_rows: list[list[str]] = []
    if pack_name:
        # 有包才有这套文件；图降级成 SVG 占位时无法转 JPEG，会回落下面的通用清单
        for slot, source in pack_slots:
            img_rows.append([
                f"图位 {slot}",
                f"{sku}.{slot}.jpg",
                "已在图片包内",
                p.image_size,
                f"源图 {source}；文件在 {pack_name} 里，文件名即 SKU + 图位，请勿改名",
            ])
    elif has_images:
        for i, fname in enumerate(ALL_IMAGE_FILES, start=1):
            group = "主图" if i <= len(IMAGE_FILES) else "详情图"
            img_rows.append([
                f"{group} {i}",
                f"images/{fname}",
                "先传平台媒体库",
                p.image_size,
                "上传后取回 URL/图片 ID 填进模板的图片列",
            ])

    blocks = [("① 模板字段 · 逐格复制", text_rows)]
    if img_rows:
        blocks.append(("② 图片 · 槽位对照", img_rows))
    blocks.append((
        "③ 使用前必读",
        [
            ["模板性质", "演示版列集合", "需卖家替换", "",
             "本表列名按公开资料整理，不是从卖家后台下载的官方模板；"
             "正式批量上传请以官方模板为准并重新映射列"],
            ["变体", "本表一行 = 一个 SKU", "需卖家补填", "",
             "有颜色/尺寸等变体时，平台要求每个变体一行（并填父体关系行），"
             "本工具未生成变体行"],
            ["留空列", "见「需卖家补填」", "需卖家补填", "",
             "这些值只有卖家或平台才有（库存、保修、类目 ID、GTIN 条码等），"
             "猜值不会让导入报错，而是把假数据写进店铺，所以刻意留空"],
        ],
    ))
    return title, meta, blocks


def _build_row(pk: str, card: dict, listing: dict, market_key: str) -> dict:
    """按平台模板字段组装一行数据。

    留空的列一律出现在「上传前必填」清单里（见 MANUAL_FIELDS）：这些是只有卖家
    或平台才有的信息，编一个值只会让它带着假数据静默通过导入。

    图片列同样留空。此前填的是 `images/xxx.png` 这种包内相对路径，而它两条路都不通：
    平台把它当 URL 抓取（抓不到）、按文件名识别（不认），结果是该列校验报错。
    真正可用的是包内的图片文件本身 —— Amazon 有按 SKU 命名的 zip 上传通道，
    其余平台是把图拖进后台的媒体库，都不需要这一格有值。
    """
    bullets = listing.get("bullet_points") or []
    price = card.get("suggested_price") or {}
    name_en = _name_en(card)
    visual = card.get("visual_features") or {}
    specs = card.get("specs") or []
    sku = make_sku(name_en, market_key)
    # 品牌只取用户显式提供的值；未提供就是 Generic。
    # 旧实现会拿商品名首词当品牌（"Sony WH-1000…" → 品牌 Sony），那是商标侵权来源
    brand = str(card.get("brand") or "Generic")

    if pk == "amazon":
        return {
            "feed_product_type": "",
            "item_sku": sku,
            "brand_name": brand,
            "item_name": listing.get("title", ""),
            "product_description": listing.get("description", ""),
            "bullet_point1": bullets[0] if len(bullets) > 0 else "",
            "bullet_point2": bullets[1] if len(bullets) > 1 else "",
            "bullet_point3": bullets[2] if len(bullets) > 2 else "",
            "bullet_point4": bullets[3] if len(bullets) > 3 else "",
            "bullet_point5": bullets[4] if len(bullets) > 4 else "",
            "generic_keywords": listing.get("search_terms", ""),
            "main_image_url": "",
            "other_image_url1": "",
            "other_image_url2": "",
            "other_image_url3": "",
            "other_image_url4": "",
            "other_image_url5": "",
            "item_type": card.get("category", ""),
            "color_name": visual.get("color", ""),
            "size_name": "",
            "part_number": f"PN-{slug(name_en).upper()}",
            "manufacturer": brand if brand != "Generic" else "",
            "product_id": "", "product_id_type": "GTIN",             "condition_type": "New",
            "standard_price": price.get("value", 39.99),
            "currency": price.get("currency", "USD"),
            "quantity": "", "fulfillment_latency": "",
            **_pkg_fields(specs),
            "country_of_origin": "",
            "warranty_description": "",
            "is_adult_product": "No", "target_gender": "",
            "recommended_browse_nodes": "",
        }

    if pk == "aliexpress":
        pkg = _pkg_fields(specs)
        return {
            "Product Title": listing.get("title", ""),
            "Product Description": listing.get("description", ""),
            "Category ID": "",
            "Product Images": "",
            "Product Attributes": ";".join(f"{s.get('name')}:{s.get('value')}" for s in specs),
            "Key Selling Points": ";".join(card.get("core_selling_points") or []),
            "Search Keywords": listing.get("search_terms", ""),
            "SKU Code": sku,
            "SKU Price": price.get("value", 39.99),
            "SKU Stock": "",
            "Currency": price.get("currency", "USD"),
            "Shipping From": "",
            "Package Length(cm)": pkg["package_length"],
            "Package Width(cm)": pkg["package_width"],
            "Package Height(cm)": pkg["package_height"],
            "Package Weight(kg)": pkg["package_weight"],
            "Brand Name": brand,
            "Warranty": "",
            "Language": listing.get("meta", {}).get("language", ""),
            "Country/Region": listing.get("meta", {}).get("label", ""),
        }

    if pk == "shopee":
        pkg = _pkg_fields(specs)
        weight_g = round(pkg["package_weight"] * 1000) if pkg["package_weight"] != "" else ""
        return {
            "商品名称": listing.get("title", ""),
            "商品描述": listing.get("description", ""),
            "类目ID": "",
            "商品价格": price.get("value", 39.99),
            "商品库存": "",
            "商品图片1": "",
            "商品图片2": "",
            "商品图片3": "",
            "商品图片4": "",
            "商品图片5": "",
            "商品规格": "；".join(f"{s.get('name')}：{s.get('value')}" for s in specs),
            "搜索关键词": listing.get("search_terms", ""),
            "商品重量(g)": weight_g,
            "包裹长(cm)": pkg["package_length"],
            "包裹宽(cm)": pkg["package_width"],
            "包裹高(cm)": pkg["package_height"],
            "品牌": brand,
            "保修期": "",
            "语言": listing.get("meta", {}).get("language", ""),
            "站点": listing.get("meta", {}).get("label", ""),
        }

    pkg = _pkg_fields(specs)
    return {  # tiktok
        "Product Name": listing.get("title", ""),
        "Product Description": listing.get("description", ""),
        "Category": "",
        "Product Images": "",
        "Price": price.get("value", 39.99),
        "Currency": price.get("currency", "USD"),
        "Stock": "",
        "SKU ID": sku,
        "SKU Name": name_en,
        "Search Keywords": listing.get("search_terms", ""),
        "Weight(kg)": pkg["package_weight"],
        "Package Length(cm)": pkg["package_length"],
        "Package Width(cm)": pkg["package_width"],
        "Package Height(cm)": pkg["package_height"],
        "Brand": brand,
        "Warranty": "",
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


def _report_md(p, card: dict, listing: dict, checks: list[dict], quality: dict,
               models: dict, row: dict, sku: str = "", pack_name: str = "",
               pack_count: int = 0, has_images: bool = False) -> str:
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
    if p.rules.asof:
        lines.append(f"- 以上数值核对时间：{p.rules.asof}")
    if p.rules.keyword_max_bytes:
        lines.append(
            f"- 搜索关键词按 **UTF-8 字节** 计上限 {p.rules.keyword_max_bytes} 字节"
            f"（当前 {utf8_len(str(listing.get('search_terms') or ''))} 字节）"
        )

    # 市场由用户自选，平台清单只是参考：勾到无站点组合时照常产出，但在这里说破
    mk_key = str(listing.get("meta", {}).get("key") or "")
    if mk_key and not market_supported(p.key, mk_key):
        lines.append(
            f"- ⚠️ **{p.name} 在「{listing.get('meta', {}).get('label', '')}」无自营站点**："
            f"本条按你勾选的市场生成，语言随市场键绑定，**仅可作素材参考，不能直接上架**"
        )

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

    if sku:
        lines.append(f"- SKU：`{sku}`（模板与图片文件名同源，改其一必须同步改另一处）")
    if pack_name:
        lines.append(
            f"- 图片包：`{pack_name}` 内 {pack_count} 张 JPEG 已按 `<SKU>.图位.jpg` 命名"
            f"（MAIN 为主图，PT01…PT08 为辅图），可**直接**在卖家后台 "
            f"Catalog · Images · Upload images 上传，这条路径不需要图片 URL；"
            f"文件名与 SKU 逐字符绑定，请勿改名"
        )
    elif has_images:
        lines.append(
            f"- 图片：`images/` 内已按 {p.image_size} 适配。平台不接受本地路径，"
            f"需先把图上传到平台媒体库（Shopee 媒体空间 / 速卖通图片银行 / TikTok 素材库）"
            f"取回 URL 或图片 ID 再填模板"
        )

    manual = _manual_for(p.key, row)
    if manual:
        lines += [
            "",
            f"## 上传前必填 · 共 {len(manual)} 项（我们不代填）",
            "以下列在模板中**故意留空**：它们要么只有卖家自己知道，要么必须由平台签发。",
            "填错不会让导入报错，而是把假数据直接写进店铺，所以宁可留空也不猜值。",
        ]
        lines += [f"- `{col}` —— {why}" for col, why in manual]
        lines += [
            "",
            "> 另：本模板的列集合是按公开资料整理的**演示版**，不是从卖家后台下载的官方模板。",
            "> 各平台的批量上传模板按类目动态生成、需登录获取，正式上传前请以官方模板为准并重新映射列。",
        ]
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
    manual_total = 0
    manual_by_unit: dict[str, list[dict]] = {}

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
        has_images = bool(params.get("with_images"))
        sku = make_sku(_name_en(card), mk)
        row = _build_row(pk, card, listing, mk)
        columns, tpl_name = TEMPLATES[pk][0], TEMPLATES[pk][1].format(mk_tag)

        # 1) 图片按平台命名规范输出（缩放/白底补边至平台 image_px 要求，线程池并行）
        fitted_map: dict[str, bytes] = {}
        if has_images:
            srcs = []
            for f in ALL_IMAGE_FILES:
                rel = f"images/{f}"
                if await storage.exists(job_id, rel):
                    srcs.append((f, rel, await storage.read(job_id, rel)))
            outs = await asyncio.gather(
                *[asyncio.to_thread(fit_image_bytes, raw, p.image_px) for _, _, raw in srcs]
            )
            for (fname, rel, raw), fitted in zip(srcs, outs):
                data = fitted if fitted is not None else raw
                await storage.save(job_id, f"{pk}/{rel}", data)
                fitted_map[fname] = data
            if fitted_map:
                # 体积直接取内存里的字节数，不必回读一遍刚写下的图
                files.append(
                    {
                        "name": f"images/（{len(fitted_map)} 张）",
                        "type": "images",
                        "size": sum(len(v) for v in fitted_map.values()),
                    }
                )

        # 1b) Amazon 专属：按 SKU 命名的 JPEG 图片包（免 URL 上传通道）
        pack_name = f"Amazon_图片包{mk_tag}.zip" if pk == "amazon" else ""
        pack_slots: list[tuple[str, str]] = []
        pack_n = 0
        if pack_name and fitted_map:
            pack = await asyncio.to_thread(_amazon_image_pack, fitted_map, sku)
            pack_slots = [(slot, source) for _, slot, source, _ in pack]
            pack_n = len(pack)
            if pack:
                blob = await asyncio.to_thread(
                    _zip_flat, {name: data for name, _, _, data in pack})
                await storage.save(job_id, f"{pk}/{pack_name}", blob)
                files.append({"name": pack_name, "type": "archive", "size": len(blob)})
                # 包已落盘，内存里的 PNG 原件不再需要（4 平台任务此处能省下上百 MB）
                fitted_map.clear()
        pack_file = pack_name if pack_n else ""

        # 2) Listing 文案
        lang = (plat.get("lang_code") or "en").upper()
        txt = _listing_txt(p, card, listing).encode("utf-8")
        await storage.save(job_id, f"{pk}/Listing_{lang}{mk_tag}.txt", txt)
        files.append({"name": f"Listing_{lang}{mk_tag}.txt", "type": "listing", "size": len(txt)})

        # 3) 批量上传模板（openpyxl / pandas 是纯 CPU 阻塞，挪到线程池）
        if pk == "amazon":
            data = await asyncio.to_thread(build_amazon_flatfile, row, columns)
        else:
            data = await asyncio.to_thread(build_csv, columns, row)
        await storage.save(job_id, f"{pk}/{tpl_name}", data)
        files.append({"name": tpl_name, "type": "template", "size": len(data)})

        # 4) 上架对照表：给在网页后台一个个表单上架的卖家（多数小卖家的真实路径）
        ws_args = _worksheet_inputs(pk, p, columns, row, card, listing, sku,
                                    has_images, pack_file, pack_slots)
        ws_bytes = await asyncio.to_thread(build_worksheet, *ws_args)
        ws_name = f"上架对照表{mk_tag}.xlsx"
        await storage.save(job_id, f"{pk}/{ws_name}", ws_bytes)
        files.append({"name": ws_name, "type": "worksheet", "size": len(ws_bytes)})

        # 5) 质量报告
        md = _report_md(p, card, listing, plat.get("checks") or [], plat.get("quality") or {},
                        models, row, sku=sku, pack_name=pack_file,
                        pack_count=pack_n, has_images=has_images)
        md_b = md.encode("utf-8")
        await storage.save(job_id, f"{pk}/质量报告{mk_tag}.md", md_b)
        files.append({"name": f"质量报告{mk_tag}.md", "type": "report", "size": len(md_b)})

        artifacts[unit_key] = files
        manual = _manual_for(pk, row)
        manual_total += len(manual)
        manual_by_unit[unit_key] = [{"col": c, "why": w} for c, w in manual]
        await emitter.log(
            f"{p.name}×{mk} 素材包已生成：{', '.join(f['name'] for f in files)}"
            + (f"（{len(manual)} 列需上传前补填）" if manual else "")
        )

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
        # 我们填不了、必须卖家上传前补填的列数（逐条清单见「上传前必填」区块）
        "manual_field_count": manual_total,
    }

    if manual_total:
        await emitter.warn(
            f"模板中有 {manual_total} 处字段我们刻意留空（类目 ID、GTIN 条码、库存、保修、"
            f"图片地址等）：这些只有卖家或平台才有，猜值会让假数据静默通过导入。"
            f"每个平台的《上架对照表》和质量报告末尾都列了逐条清单，补齐后再上传"
        )

    # 用户自选的"该平台×市场"组合若本站点并不存在，照样生成但必须说破（见 api/jobs.py）
    for note in (params.get("market_notes") or []):
        await emitter.warn(note)

    await emitter.step(S_PACK, "done", f"{len(platforms)} 份站点素材包已就绪")
    # 待补清单并入任务单元回给前端：预览页要在这里公示，
    # 否则卖家看到一堆空单元格，只会以为生成失败了
    out_platforms = [
        {**x, "manual_fields": manual_by_unit.get(x.get("key"), [])} for x in platforms
    ]
    return {"artifacts": artifacts, "report": report, "platforms": out_platforms}
