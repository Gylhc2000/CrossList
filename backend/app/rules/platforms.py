"""平台规则知识库 + 批量上传模板字段定义

对应方案「模块E：平台规则知识库 + 批量上传格式生成」。
模板字段为简化演示版，覆盖各平台公开模板的高频必填项；
接入真实模板时只需修改本文件中的 *_COLUMNS / *_FIELD_NOTES。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


def _is_plain_ascii_word(w: str) -> bool:
    """仅含 ASCII 字母（允许连字符）的单词，可用词边界精确匹配"""
    cleaned = w.replace("-", "")
    return bool(cleaned) and cleaned.isascii() and cleaned.isalpha()


_WORD_RE: dict[str, "re.Pattern[str]"] = {}


def _banned_hit(word: str, haystack: str) -> bool:
    """禁用词命中判断：ASCII 单词用词边界 + 常见词形变化匹配，避免 cure 误中 secure/manicure。

    例如 cure 同时命中 cure/cures/cured/curing，但 secure/manicure/procure 因首字母前是字母、
    不存在词边界而不会误中；短语、含标点（如 "100% safe"、"#1"）及中文/韩文词条仍用子串匹配。
    """
    wl = word.lower()
    if _is_plain_ascii_word(wl):
        pat = _WORD_RE.get(wl) or _WORD_RE.setdefault(
            wl, re.compile(rf"\b{re.escape(wl)}(?:s|d|ing)?(?![a-z])", re.ASCII)
        )
        return bool(pat.search(haystack))
    return wl in haystack


# ---------------- 市场 ----------------
@dataclass(frozen=True)
class Market:
    key: str
    label: str
    flag: str
    language: str
    lang_code: str


MARKETS: list[Market] = [
    Market("us", "美国", "🇺🇸", "英语", "en"),
    Market("kr", "韩国", "🇰🇷", "韩语", "ko"),
    Market("br", "巴西", "🇧🇷", "葡萄牙语", "pt"),
    Market("jp", "日本", "🇯🇵", "日语", "ja"),
    Market("de", "德国", "🇩🇪", "德语", "de"),
    Market("es", "西班牙", "🇪🇸", "西班牙语", "es"),
]

MARKET_BY_KEY = {m.key: m for m in MARKETS}
MARKET_BY_LANG = {m.lang_code: m for m in MARKETS}


# ---------------- 禁用词 ----------------
BANNED_WORDS: list[str] = [
    "cure", "treatment", "medical", "heal", "therapy", "anti-bacterial",
    "100% safe", "best", "cheapest", "#1", "no.1", "free gift",
    "guaranteed", "fda approved",
    "치료", " 의료", "완치", "최고",
    "curar", "médico", "tratamiento", "el mejor",
]

# 竞品/IP 蹭词：非本品牌却在标题/关键词里堆砌其他 IP 或品牌名，
# 属商标侵权与误导性关键词，各平台查得都较严。演示集覆盖高频词，按类目扩充。
COMPETITOR_WORDS: list[str] = [
    "仮面ライダー", "ガンダム", "戦隊", "トミカ", "プラレール", "プリキュア",
    "ポケモン", "ピカチュウ", "ドラえもん", "ワンピース", "鬼滅",
    "pokemon", "pokémon", "pikachu", "lego", "disney", "marvel",
    "bandai", "hot wheels", "barbie", "transformers",
]


# ---------------- 平台 ----------------
@dataclass(frozen=True)
class PlatformRules:
    title_max: int
    bullet_max: int
    bullet_count: int
    keyword_max: int
    desc_max: int
    main_image: str


@dataclass(frozen=True)
class Platform:
    key: str
    name: str
    default_market: str
    image_size: str
    image_px: int
    rules: PlatformRules
    file_ext: str  # xlsx | csv


PLATFORMS: dict[str, Platform] = {
    "amazon": Platform(
        key="amazon", name="Amazon", default_market="us",
        image_size="2000×2000px", image_px=2000, file_ext="xlsx",
        rules=PlatformRules(
            title_max=200, bullet_max=500, bullet_count=5,
            keyword_max=250, desc_max=2000,
            main_image="纯白背景（RGB 255,255,255），商品占比≥85%，无水印/文字/边框",
        ),
    ),
    "aliexpress": Platform(
        key="aliexpress", name="AliExpress", default_market="kr",
        image_size="800×800px", image_px=800, file_ext="csv",
        rules=PlatformRules(
            title_max=128, bullet_max=300, bullet_count=5,
            keyword_max=200, desc_max=4000,
            main_image="白底或浅色背景，建议 800×800 以上，主图不得含促销文字",
        ),
    ),
    "shopee": Platform(
        key="shopee", name="Shopee", default_market="br",
        image_size="1024×1024px", image_px=1024, file_ext="csv",
        rules=PlatformRules(
            title_max=120, bullet_max=200, bullet_count=5,
            keyword_max=150, desc_max=3000,
            main_image="正方形主图，最多 9 张，首图建议白底，禁止联系方式与站外信息",
        ),
    ),
    "tiktok": Platform(
        key="tiktok", name="TikTok Shop", default_market="us",
        image_size="1080×1080px", image_px=1080, file_ext="csv",
        rules=PlatformRules(
            title_max=150, bullet_max=250, bullet_count=4,
            keyword_max=150, desc_max=2000,
            main_image="1:1 主图，禁止夸张绝对化用语与站外引流信息",
        ),
    ),
}


# ---------------- 语言纯度校验 ----------------
# LLM 长文本生成时偶发"语种串台"：韩语文案里混入中文词（如"摆拍"）、
# 西班牙语文案里混入英文整句等。这类错误规则库此前完全检测不到，
# 只能靠 LLM 评分时"肉眼"发现并扣分，且因为是 warn 级不会触发自动修正，
# 最终带着 60 分直接导出。这里按 Unicode 字符块做硬检测，命中即为 error。
def _script_of(ch: str) -> str:
    """返回字符所属书写系统：latin / hangul / kana / han / cyrillic / greek / other"""
    o = ord(ch)
    if o < 0x80:
        return "latin"
    if 0x00A0 <= o <= 0x024F or 0x1E00 <= o <= 0x1EFF or 0xFF00 <= o <= 0xFFEF:
        return "latin"          # 拉丁扩展 + 全角字母数字
    if 0x0370 <= o <= 0x03FF:
        return "greek"
    if 0x0400 <= o <= 0x04FF:
        return "cyrillic"
    if (0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F or 0xA960 <= o <= 0xA97F
            or 0xAC00 <= o <= 0xD7A3):
        return "hangul"
    if 0x3040 <= o <= 0x30FF or 0x31F0 <= o <= 0x31FF:
        return "kana"
    if 0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF:
        return "han"
    return "other"


# 各目标语言允许出现的书写系统。common（数字/标点/符号）恒放行。
# 注意 ja 必须放行 han：汉字是日语正字法的一部分，不能当成"混入中文"。
_ALLOWED_SCRIPTS: dict[str, set[str]] = {
    "en": {"latin"},
    "de": {"latin"},
    "es": {"latin"},
    "pt": {"latin"},
    "fr": {"latin"},
    "ko": {"hangul", "latin"},
    "ja": {"kana", "han", "latin"},
}

_SCRIPT_LABEL = {
    "han": "中文汉字", "hangul": "韩文", "kana": "日文假名",
    "cyrillic": "西里尔字母", "greek": "希腊字母", "other": "非目标语言字符",
}


def find_foreign_chars(text: str, lang_code: str) -> dict[str, list[str]]:
    """检测文本中不属于目标语言的字符，返回 {脚本名: [命中字符]}"""
    allowed = _ALLOWED_SCRIPTS.get(lang_code)
    if not allowed or not text:
        return {}
    hits: dict[str, list[str]] = {}
    for ch in text:
        if ch.isspace() or ch.isdigit():
            continue
        s = _script_of(ch)
        if s in allowed:
            continue
        # 标点/符号（含中韩日共用的全角标点、通用标点）、箭头、emoji 均非文字系统，
        # 不构成语种串台。TikTok/Shopee 标题里 emoji 属常见用法，误判会白白触发一轮重写。
        if s == "other" and (
            0x0300 <= ord(ch) <= 0x036F      # 组合变音符号
            or 0x2000 <= ord(ch) <= 0x303F   # 通用/全角标点
            or 0x2190 <= ord(ch) <= 0x21FF   # 箭头
            or 0x2600 <= ord(ch) <= 0x27BF   # 杂项与装饰符号
            or 0x2B00 <= ord(ch) <= 0x2BFF   # 杂项符号与箭头
            or 0x1F000 <= ord(ch) <= 0x1FAFF  # emoji
            or ord(ch) in (0xFE0F, 0x20E3)   # 变体选择符 / keycap
        ):
            continue
        hits.setdefault(s, [])
        if ch not in hits[s]:
            hits[s].append(ch)
    return hits


def foreign_chars_msg(text: str, lang_code: str, language: str = "") -> str:
    """把命中结果拼成可读文案，例如：混入非韩语字符：摆、拍"""
    hits = find_foreign_chars(text, lang_code)
    if not hits:
        return ""
    parts = []
    for s, chars in hits.items():
        parts.append(f"{_SCRIPT_LABEL.get(s, s)}「{''.join(chars[:8])}」")
    return "混入" + (language or "非目标") + "以外的字符：" + "、".join(parts)


# ---------------- 整段中文检测（日语专用补充） ----------------
# 字符块检测对 ja 无效：汉字是日语正字法的一部分，Han 被整体放行，
# 但模型偶尔会整段滑成中文（用户实测出现过两整段中文描述）。
# 这里按"段落"做两个无法误伤正常日文的判定：
#   1) 段内 Han>=8 且无假名、无 ASCII 数字/字母 —— 正常日文段落必有假名连接
#      或数字单位；规格列举段（如「材質：PVC 18cm」）含 ASCII 被排除。
#   2) 命中简体中文特有字（日语正字法几乎不用）累计 >=3 次。
import re as _re

_ZH_MARKERS = "了这们吗吧呢嘛咱您啥咋俩"
_SEG_SPLIT = _re.compile(r"[\n。！？；!?\;]+")


def find_chinese_runs(text: str) -> str:
    """返回第一个疑似整段中文段落的描述，无则返回空串。仅用于允许 Han 的语言。"""
    if not text:
        return ""
    for seg in _SEG_SPLIT.split(text):
        seg = seg.strip()
        if not seg:
            continue
        han = sum(1 for ch in seg if _script_of(ch) == "han")
        kana = sum(1 for ch in seg if _script_of(ch) == "kana")
        has_alnum = any(ch.isascii() and ch.isalnum() for ch in seg)
        if han >= 8 and kana == 0 and not has_alnum:
            return f"段落「{seg[:14]}…」为无假名连接的纯汉字段落，疑似整段中文"
        marker_hits = [c for c in _ZH_MARKERS if c in seg]
        if sum(seg.count(c) for c in marker_hits) >= 3:
            return f"段落「{seg[:14]}…」命中中文特有字「{'、'.join(marker_hits[:4])}」，疑似整段中文"
    return ""


# ---------------- 规则校验 ----------------
@dataclass
class Issue:
    level: str          # error | warn
    field: str
    msg: str
    fix: str = ""

    def to_dict(self) -> dict:
        return {"level": self.level, "field": self.field, "msg": self.msg, "fix": self.fix}


def check_text(listing: dict, rules: PlatformRules,
               lang_code: str = "", language: str = "") -> list[Issue]:
    """规则校验：标题/卖点/关键词长度 + 禁用词过滤 + 目标语言纯度"""
    issues: list[Issue] = []

    title = str(listing.get("title") or "")
    if not title:
        issues.append(Issue("error", "title", "标题为空", "必须生成标题"))
    elif len(title) > rules.title_max:
        issues.append(Issue(
            "error", "title",
            f"标题 {len(title)} 字符，超出平台上限 {rules.title_max}",
            f"精简至 {rules.title_max} 字符以内",
        ))
    elif len(title) > rules.title_max * 0.95:
        issues.append(Issue("warn", "title", f"标题 {len(title)} 字符，接近上限 {rules.title_max}", "建议适当精简"))

    bullets = listing.get("bullet_points") or []
    if len(bullets) < min(3, rules.bullet_count):
        issues.append(Issue("warn", "bullet_points", f"五点描述仅 {len(bullets)} 条", f"建议补齐至 {rules.bullet_count} 条"))
    for i, b in enumerate(bullets):
        if len(str(b)) > rules.bullet_max:
            issues.append(Issue("error", f"bullet_{i+1}",
                                f"第 {i+1} 条要点 {len(str(b))} 字符，超出 {rules.bullet_max}",
                                "精简该条要点"))

    kw = str(listing.get("search_terms") or listing.get("keywords") or "")
    if not kw:
        issues.append(Issue("warn", "keywords", "未提供搜索关键词", "补充 5-10 个高相关关键词"))
    elif len(kw) > rules.keyword_max:
        issues.append(Issue("error", "keywords",
                            f"搜索词 {len(kw)} 字符，超出 {rules.keyword_max}",
                            "删减重复与无效词"))

    desc = str(listing.get("description") or "")
    if not desc:
        issues.append(Issue("warn", "description", "商品描述为空", "补充完整商品描述"))
    elif len(desc) > rules.desc_max:
        issues.append(Issue("warn", "description", f"描述 {len(desc)} 字符，超出建议值 {rules.desc_max}", "精简描述"))

    haystack = " ".join([title, *[str(b) for b in bullets], desc, kw]).lower()
    hits = [w for w in BANNED_WORDS if _banned_hit(w, haystack)]
    if hits:
        issues.append(Issue("error", "banned_words",
                            f"命中禁用词：{'、'.join(hits)}",
                            "替换为中性表述，删除医疗声称与绝对化用语"))
    c_hits = [w for w in COMPETITOR_WORDS if _banned_hit(w, haystack)]
    if c_hits:
        issues.append(Issue("error", "competitor_words",
                            f"命中竞品/IP 词：{'、'.join(c_hits[:5])}",
                            "删除竞品品牌词，仅保留本商品自身的名称与通用品类词"))

    # 语言纯度：逐字段定位，报出具体字符，便于前端提示与 fix 精准重写
    if lang_code:
        for field, text in (("title", title), ("description", desc), ("keywords", kw)):
            if not text:
                continue
            t = str(text)
            if find_foreign_chars(t, lang_code):
                issues.append(Issue(
                    "error", field,
                    f"{field} 字段{foreign_chars_msg(t, lang_code, language)}",
                    f"全部改写为{language or '目标语言'}表达，禁止混入其他语言文字",
                ))
            elif lang_code == "ja" and find_chinese_runs(t):
                # 字符块检测放行日语汉字，整段中文由段落级检测兜底
                issues.append(Issue(
                    "error", field,
                    f"{field} 字段{find_chinese_runs(t)}",
                    "将该段落翻译为日语，确保全篇语言统一",
                ))
        for i, b in enumerate(bullets):
            if b and find_foreign_chars(str(b), lang_code):
                issues.append(Issue(
                    "error", f"bullet_{i+1}",
                    f"第 {i+1} 条卖点{foreign_chars_msg(str(b), lang_code, language)}",
                    f"全部改写为{language or '目标语言'}表达，禁止混入其他语言文字",
                ))
    return issues


# ---------------- 批量上传模板字段 ----------------
AMAZON_COLUMNS: list[str] = [
    "feed_product_type", "item_sku", "brand_name", "item_name", "product_description",
    "bullet_point1", "bullet_point2", "bullet_point3", "bullet_point4", "bullet_point5",
    "generic_keywords", "main_image_url", "other_image_url1", "other_image_url2",
    "other_image_url3", "other_image_url4", "other_image_url5",
    "item_type", "color_name", "size_name", "part_number", "manufacturer",
    "product_id", "product_id_type", "condition_type", "standard_price", "currency",
    "quantity", "fulfillment_latency", "package_length", "package_width", "package_height",
    "package_weight", "package_length_unit", "package_weight_unit", "country_of_origin",
    "warranty_description", "is_adult_product", "target_gender", "recommended_browse_nodes",
]

AMAZON_FIELD_NOTES: dict[str, str] = {
    "feed_product_type": "商品类型（平台枚举值）",
    "item_sku": "卖家自定义SKU，唯一",
    "brand_name": "品牌名",
    "item_name": "商品标题（Listing Title）",
    "product_description": "商品描述",
    "bullet_point1": "卖点1", "bullet_point2": "卖点2", "bullet_point3": "卖点3",
    "bullet_point4": "卖点4", "bullet_point5": "卖点5",
    "generic_keywords": "搜索关键词，空格分隔",
    "main_image_url": "主图路径（白底）",
    "other_image_url1": "辅图1", "other_image_url2": "辅图2", "other_image_url3": "辅图3",
    "other_image_url4": "辅图4", "other_image_url5": "辅图5",
    "item_type": "商品类型关键词", "color_name": "颜色", "size_name": "尺寸",
    "part_number": "型号", "manufacturer": "制造商",
    "product_id": "商品编码（UPC/EAN/GTIN）", "product_id_type": "编码类型",
    "condition_type": "商品状况（New）", "standard_price": "售价", "currency": "币种",
    "quantity": "库存数量", "fulfillment_latency": "发货时效（天）",
    "package_length": "包装长", "package_width": "包装宽", "package_height": "包装高",
    "package_weight": "包装重量", "package_length_unit": "长度单位",
    "package_weight_unit": "重量单位", "country_of_origin": "原产国",
    "warranty_description": "保修说明", "is_adult_product": "是否成人用品",
    "target_gender": "目标性别", "recommended_browse_nodes": "推荐类目节点",
}

ALIEXPRESS_COLUMNS: list[str] = [
    "Product Title", "Product Description", "Category ID", "Product Images",
    "Product Attributes", "Key Selling Points", "Search Keywords",
    "SKU Code", "SKU Price", "SKU Stock", "Currency", "Shipping From",
    "Package Length(cm)", "Package Width(cm)", "Package Height(cm)", "Package Weight(kg)",
    "Brand Name", "Warranty", "Language", "Country/Region",
]

SHOPEE_COLUMNS: list[str] = [
    "商品名称", "商品描述", "类目ID", "商品价格", "商品库存",
    "商品图片1", "商品图片2", "商品图片3", "商品图片4", "商品图片5",
    "商品规格", "搜索关键词", "商品重量(g)", "包裹长(cm)", "包裹宽(cm)", "包裹高(cm)",
    "品牌", "保修期", "语言", "站点",
]

TIKTOK_COLUMNS: list[str] = [
    "Product Name", "Product Description", "Category", "Product Images",
    "Price", "Currency", "Stock", "SKU ID", "SKU Name",
    "Search Keywords", "Weight(kg)", "Package Length(cm)", "Package Width(cm)",
    "Package Height(cm)", "Brand", "Warranty", "Language", "Region",
]
