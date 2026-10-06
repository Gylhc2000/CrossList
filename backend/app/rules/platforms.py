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
# 不要建 lang_code -> Market 的反查表：同一语言可以挂多个市场（葡语的巴西/葡萄牙、
# 英语的美国/英国），那种表会静默覆盖，校验与导出取到的市场标签就成了别的站点。


# ---------------- 禁用词 ----------------
BANNED_WORDS: list[str] = [
    "cure", "treatment", "medical", "heal", "therapy", "anti-bacterial",
    "100% safe", "best", "cheapest", "#1", "no.1", "free gift",
    "guaranteed", "fda approved",
    # 非 ASCII 词条走子串匹配，多一个前导空格就永远匹配不上句首或标点后的词
    # （"의료기기 사용" 开头就没有空格）
    "치료", "의료", "완치", "최고",
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


# ---------------- 长度计量 ----------------
def utf8_len(s: str) -> int:
    """UTF-8 字节数。

    部分平台的部分字段按**字节**而非字符限长（已知：Amazon 后台关键词 250 字节）。
    韩语/日语一个字符占 3 字节，按字符校验会静默漏判：84 个韩文字符 = 252 字节，
    代码判"合规"，平台直接拒。凡声明了 keyword_max_bytes 的规则一律用本函数计量。
    """
    return len((s or "").encode("utf-8"))


def truncate_utf8(s: str, max_bytes: int) -> str:
    """按 UTF-8 字节上限截断，尽量在空格边界断开（关键词是空格分隔的词表）。"""
    s = (s or "").strip()
    if utf8_len(s) <= max_bytes:
        return s
    out = ""
    for word in s.split(" "):
        cand = f"{out} {word}".strip()
        if utf8_len(cand) > max_bytes:
            break
        out = cand
    if out:
        return out
    # 单个词就超限：逐字退到字节上限内
    cut = ""
    for ch in s:
        if utf8_len(cut + ch) > max_bytes:
            break
        cut += ch
    return cut


# ---------------- 平台 ----------------
@dataclass(frozen=True)
class PlatformRules:
    title_max: int
    bullet_max: int
    bullet_count: int
    keyword_max: int
    desc_max: int
    main_image: str
    # 关键词字段的计量口径：非 None 表示平台按 UTF-8 字节限长（此时 keyword_max 按字节解释）
    keyword_max_bytes: int | None = None
    # 这些数字是某个时点抓的快照，平台会改（Amazon 标题上限 2026-07-27 就从 200 降到 75）。
    # 无此标记时无法判断一条规则是"平台如此规定"还是"我们三年没看过它"。
    asof: str = ""


@dataclass(frozen=True)
class Platform:
    key: str
    name: str
    default_market: str
    image_size: str
    image_px: int
    rules: PlatformRules
    file_ext: str  # xlsx | csv
    # 该平台真实运营消费者站点的市场（限上面 MARKETS 里有的键）。
    # 不填这层就会生成出"根本不存在的组合"：市场键绑死语言
    # （kr→韩语），勾了 Amazon×韩国就等于给一个不存在的站点写韩语 Listing。
    # ⚠️ 这份清单需要拿平台官方站点列表复核，尤其 TikTok 的欧洲站点在 2025-2026 仍在扩张。
    markets: tuple[str, ...] = ()


PLATFORMS: dict[str, Platform] = {
    "amazon": Platform(
        key="amazon", name="Amazon", default_market="us",
        image_size="2000×2000px", image_px=2000, file_ext="xlsx",
        markets=("us", "de", "jp", "es", "br"),
        rules=PlatformRules(
            # 2026-07-27 起 item_name 上限由 200 降为 75 字符（媒体类除外），
            # 且超限不是拒单、而是亚马逊用 AI 自动重写标题 —— 按 200 生成等于把
            # 我们写好的标题交给对方的模型改写。来源为公开卖家资讯，未逐站点核实。
            title_max=75, bullet_max=500, bullet_count=5,
            keyword_max=250, keyword_max_bytes=250,   # generic keywords 限 250 **字节**
            desc_max=2000,
            main_image="纯白背景（RGB 255,255,255），商品占比≥85%，无水印/文字/边框",
            asof="2026-10-01 核对标题/关键词上限；其余为早期值，待复核",
        ),
    ),
    "aliexpress": Platform(
        key="aliexpress", name="AliExpress", default_market="kr",
        image_size="800×800px", image_px=800, file_ext="csv",
        markets=("us", "kr", "br", "jp", "de", "es"),
        rules=PlatformRules(
            title_max=128, bullet_max=300, bullet_count=5,
            keyword_max=200, desc_max=4000,
            main_image="白底或浅色背景，建议 800×800 以上，主图不得含促销文字",
            # 开放平台文档称 subject 须为 ASCII（1-128），若为真则与本平台的韩语
            # 默认市场直接冲突；该说法未在批量模板路径上核实，标 UNCERTAIN，留此备忘。
            asof="2026-10-01 标注 ASCII 标题疑点；上限数值待复核",
        ),
    ),
    "shopee": Platform(
        key="shopee", name="Shopee", default_market="br",
        image_size="1024×1024px", image_px=1024, file_ext="csv",
        markets=("br",),   # 东南亚/台/拉美，无美国、韩国、日本、德国、西班牙站点
        rules=PlatformRules(
            title_max=120, bullet_max=200, bullet_count=5,
            keyword_max=150, desc_max=3000,
            main_image="正方形主图，最多 9 张，首图建议白底，禁止联系方式与站外信息",
            asof="待复核（数值自项目初期未更新）",
        ),
    ),
    "tiktok": Platform(
        key="tiktok", name="TikTok Shop", default_market="us",
        image_size="1080×1080px", image_px=1080, file_ext="csv",
        markets=("us", "br", "jp", "de", "es"),   # 无韩国站点
        rules=PlatformRules(
            title_max=150, bullet_max=250, bullet_count=4,
            keyword_max=150, desc_max=2000,
            main_image="1:1 主图，禁止夸张绝对化用语与站外引流信息",
            asof="待复核（数值自项目初期未更新）",
        ),
    ),
}


def market_supported(pk: str, mk: str) -> bool:
    """该平台是否在此市场有站点。markets 为空视为不限制（新平台还没填）。"""
    p = PLATFORMS.get(pk)
    return True if not p or not p.markets else mk in p.markets


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
    elif rules.keyword_max_bytes:
        # 按字节判：韩语 84 字符 = 252 字节，按字符校验会静默放过、平台直接拒
        n = utf8_len(kw)
        if n > rules.keyword_max_bytes:
            issues.append(Issue("error", "keywords",
                                f"搜索词 {n} 字节（{len(kw)} 字符），超出平台上限 "
                                f"{rules.keyword_max_bytes} 字节",
                                "按字节删减关键词：非拉丁文字一个字符通常占 2~3 字节"))
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
    "feed_product_type": "商品类型（平台枚举值，决定必填列集合）· 需卖家填",
    "item_sku": "卖家自定义SKU，唯一",
    "brand_name": "品牌名",
    "item_name": "商品标题（Listing Title）",
    "product_description": "商品描述",
    "bullet_point1": "卖点1", "bullet_point2": "卖点2", "bullet_point3": "卖点3",
    "bullet_point4": "卖点4", "bullet_point5": "卖点5",
    "generic_keywords": "搜索关键词，空格分隔，上限按 UTF-8 字节计",
    "main_image_url": "主图路径（白底）",
    "other_image_url1": "辅图1", "other_image_url2": "辅图2", "other_image_url3": "辅图3",
    "other_image_url4": "辅图4", "other_image_url5": "辅图5",
    "item_type": "商品类型关键词", "color_name": "颜色",
    "size_name": "尺寸 · 需卖家填（平台尺码须落在类目允许值内）",
    "part_number": "型号 · 需卖家填（须与实物铭牌一致，我们不代填）", "manufacturer": "制造商",
    "product_id": "商品编码（UPC/EAN/GTIN）· 需卖家填，无法生成",
    "product_id_type": "编码类型",
    "condition_type": "商品状况（New）", "standard_price": "售价", "currency": "币种",
    "quantity": "库存数量 · 需卖家填", "fulfillment_latency": "发货时效（天）· 需卖家填",
    "package_length": "包装长", "package_width": "包装宽", "package_height": "包装高",
    "package_weight": "包装重量", "package_length_unit": "长度单位",
    "package_weight_unit": "重量单位",
    "country_of_origin": "原产国 · 需卖家填（影响清关/关税）",
    "warranty_description": "保修说明 · 需卖家填（对消费者的法律承诺）",
    "is_adult_product": "是否成人用品",
    "target_gender": "目标性别 · 需卖家填",
    "recommended_browse_nodes": "推荐类目节点 · 需卖家填（平台数字 ID）",
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
