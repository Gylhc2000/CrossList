"""交付物生成：SKU 同源、图片列留空、待补清单、包裹参数解析、图片包命名

这些判定是"下载即可用"承诺的地基，回归一次就值回整轮人工核对。
"""
import io
import zipfile

from PIL import Image

from app.agent.nodes.export import (
    ALL_IMAGE_FILES,
    AMZ_SLOTS,
    IMAGE_COLUMNS,
    MANUAL_FIELDS,
    TEMPLATES,
    _amazon_image_pack,
    _build_row,
    _is_placeholder,
    _manual_for,
    _pkg_fields,
    _zip_flat,
    make_sku,
)

CARD = {
    "product_name_zh": "无线耳机",
    "product_name_en": "Sony WH-1000XM5",
    "category": "Electronics",
    "brand": "",
    "visual_features": {"color": "Black"},
    "specs": [{"name": "外形尺寸", "value": "30×20×5 cm"},
              {"name": "净重", "value": "250g"}],
    "suggested_price": {"currency": "USD", "value": 39.99},
    "core_selling_points": ["a", "b"],
}
LISTING = {
    "title": "T", "description": "D", "search_terms": "kw",
    "bullet_points": ["1", "2"],
    "meta": {"key": "us", "label": "美国", "language": "英语"},
}


def _png(size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return buf.getvalue()


# ---------------- SKU ----------------
def test_make_sku_is_market_unique_and_filename_safe():
    us = make_sku("Sony WH-1000XM5", "us")
    de = make_sku("Sony WH-1000XM5", "de")
    assert us == "CL-SONY_WH-1000XM5-US"
    # 同平台多市场撞 SKU 会在店铺里互相覆盖；Amazon 还靠文件名里的 SKU 认商品
    assert us != de
    for ch in '\\/:*?"<>| ':
        assert ch not in us


# ---------------- 模板列与行 ----------------
def test_row_keys_match_template_columns_exactly():
    for pk, (columns, _) in TEMPLATES.items():
        row = _build_row(pk, CARD, LISTING, "us")
        # 行里有、表头没有 → 这格被静默丢弃；反之 → 凭空多一个空列
        assert set(row) == set(columns), pk


def test_manual_field_names_all_exist_in_templates():
    for pk, notes in MANUAL_FIELDS.items():
        columns = set(TEMPLATES[pk][0])
        assert set(notes) <= columns, f"{pk}: {sorted(set(notes) - columns)}"


# ---------------- 图片列一律留空 ----------------
def test_image_columns_are_left_blank_for_every_platform():
    for pk, cols in IMAGE_COLUMNS.items():
        row = _build_row(pk, CARD, LISTING, "us")
        for c in cols:
            # 包内相对路径既不是平台可抓取的 URL，也不被当作文件名识别，
            # 填了只会让该列校验报错；槽位对应关系写在上架对照表里
            assert row[c] == "", f"{pk}.{c}"


def test_placeholder_columns_are_the_ones_in_the_manual_checklist():
    row = _build_row("amazon", CARD, LISTING, "us")
    manual = dict(_manual_for("amazon", row))
    assert {"quantity", "product_id", "main_image_url"} <= set(manual)
    assert all(_is_placeholder(row[c]) for c in manual)
    assert not _is_placeholder(row["item_name"])      # 有值的列不该混进清单


# ---------------- 包裹参数：提不到就留空，不猜 ----------------
def test_pkg_fields_parses_dims_and_weight():
    pkg = _pkg_fields(CARD["specs"])
    assert (pkg["package_length"], pkg["package_width"], pkg["package_height"]) == (30.0, 20.0, 5.0)
    assert pkg["package_weight"] == 0.25              # 250 g
    assert pkg["package_length_unit"] == "CM"


def test_pkg_fields_leaves_missing_values_empty():
    pkg = _pkg_fields([{"name": "材质", "value": "ABS"}])
    assert pkg["package_weight"] == "" and pkg["package_length"] == ""
    assert pkg["package_weight_unit"] == ""


def test_pkg_fields_refuses_unitless_weight():
    """「重量：0.25」这类填法多半是 kg，按克猜会把运费/FBA 重量算错 1000 倍。

    旧实现在无单位时默认按克处理，猜错不会让导入报错，只会静默写进店铺，
    与「宁缺勿猜」冲突 —— 现在一律留空，由卖家补。
    """
    for specs in ([{"name": "重量", "value": "0.25"}],
                  [{"name": "净重", "value": "1 斤"}],
                  [{"name": "weight", "value": "300"}]):
        pkg = _pkg_fields(specs)
        assert pkg["package_weight"] == "", specs
        assert pkg["package_weight_unit"] == "", specs


def test_amazon_row_does_not_invent_part_number():
    """型号只能来自实物：以前拿英文商品名拼 PN-XXX，是编号造假而非填表。

    非空的假值会带着「可直接粘贴」的状态进对照表，卖家无从发现，所以它必须
    留空并出现在「需卖家补填」清单里（与保修、库存同一条纪律）。
    """
    row = _build_row("amazon", CARD, LISTING, "us")
    assert row["part_number"] == ""
    assert "part_number" in MANUAL_FIELDS["amazon"]
    assert "part_number" in dict(_manual_for("amazon", row))


# ---------------- Amazon 图片包 ----------------
def test_image_pack_filenames_pair_sku_with_slot():
    images = {f: _png() for f in ALL_IMAGE_FILES}
    pack = _amazon_image_pack(images, "CL-X-US")
    assert [name for name, _slot, _src, _b in pack] == [
        f"CL-X-US.{slot}.jpg" for slot in AMZ_SLOTS
    ]
    assert all(blob[:2] == b"\xff\xd8" for *_ , blob in pack)   # JPEG SOI 头


def test_image_pack_skips_missing_images():
    pack = _amazon_image_pack({"main_1_white.png": _png()}, "CL-X-US")
    assert len(pack) == 1 and pack[0][1] == "MAIN"


def test_image_pack_ignores_unreadable_bytes():
    # 生成失败的降级占位图是 SVG，PIL 读不了 → 该图位不出现（对照表据此少列一行）
    assert _amazon_image_pack({"main_1_white.png": b"<svg/>"}, "S") == []


def test_zip_flat_keeps_entries_at_root():
    blob = _zip_flat({"CL-X-US.MAIN.jpg": b"a", "CL-X-US.PT01.jpg": b"b"})
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert sorted(z.namelist()) == ["CL-X-US.MAIN.jpg", "CL-X-US.PT01.jpg"]
        assert z.read("CL-X-US.MAIN.jpg") == b"a"
