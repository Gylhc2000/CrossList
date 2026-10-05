"""规则库：长度计量口径、市场组合、禁用词、语言纯度

check_text 及其辅助函数都是纯计算、且 import 链上不依赖任何第三方包，
适合当回归网（本项目其余部分需要装了依赖才能 import）。
"""
from app.rules.platforms import (
    PLATFORMS,
    check_text,
    find_chinese_runs,
    find_foreign_chars,
    market_supported,
    truncate_utf8,
    utf8_len,
)


def _listing(**kw):
    base = {
        "title": "Wireless Earbuds",
        "bullet_points": ["a", "b", "c", "d", "e"],
        "description": "desc",
        "search_terms": "earbuds wireless",
    }
    base.update(kw)
    return base


# ---------------- 字节口径 ----------------
def test_utf8_len_counts_bytes_not_chars():
    assert utf8_len("abc") == 3
    assert utf8_len("耳机") == 6          # 一个 CJK 字符 3 字节
    assert utf8_len("") == 0


def test_truncate_utf8_stays_inside_budget_and_on_word_boundary():
    words = "무선 이어폰 노캔 방수".split()
    full = " ".join(words * 40)
    out = truncate_utf8(full, 60)
    assert 0 < utf8_len(out) <= 60
    assert full.startswith(out)           # 只能整词丢弃，不能把词咬断


def test_truncate_utf8_survives_single_oversized_word():
    assert utf8_len(truncate_utf8("가" * 100, 9)) <= 9


# ---------------- 平台 × 市场组合 ----------------
def test_impossible_market_combos_are_rejected():
    assert market_supported("amazon", "kr") is False      # Amazon 没有韩国站
    assert market_supported("shopee", "us") is False
    assert market_supported("shopee", "br") is True
    assert market_supported("tiktok", "kr") is False


def test_platform_with_no_market_list_does_not_block():
    # markets 未填 = 不限制，别让新平台被自己漏配的字段卡死
    assert market_supported("nonexistent", "us") is True


# ---------------- 标题与关键词上限 ----------------
def test_amazon_title_over_75_is_error():
    issues = check_text(_listing(title="x" * 76), PLATFORMS["amazon"].rules)
    assert any(i.level == "error" and i.field == "title" for i in issues)


def test_amazon_keyword_limit_is_measured_in_bytes():
    rules = PLATFORMS["amazon"].rules
    # 84 个韩文字符 = 252 字节：按字符放行、按字节拒绝，正是历史上漏判的那类
    terms = " ".join(["무선"] * 42)
    assert len(terms) < rules.keyword_max_bytes
    assert utf8_len(terms) > rules.keyword_max_bytes
    hits = [i for i in check_text(_listing(search_terms=terms), rules) if i.field == "keywords"]
    assert hits and "字节" in hits[0].msg


def test_banned_word_at_string_start_matches():
    # 这条曾经写成 " 의료"（带前导空格）：非 ASCII 走子串匹配，句首永远命不中
    issues = check_text(_listing(title="의료 기기"), PLATFORMS["tiktok"].rules)
    assert any(i.field == "banned_words" and "의료" in i.msg for i in issues)


def test_banned_word_does_not_match_inside_another_word():
    issues = check_text(_listing(title="Secure fast charging"), PLATFORMS["amazon"].rules)
    assert not any(i.field == "banned_words" for i in issues)


def test_competitor_word_is_a_hard_error():
    issues = check_text(_listing(title="Pokemon style earbuds"), PLATFORMS["amazon"].rules)
    assert any(i.field == "competitor_words" for i in issues)


# ---------------- 语言纯度 ----------------
def test_chinese_chars_leaking_into_korean_title():
    assert find_foreign_chars("블루투스 이어폰 摆拍", "ko")
    issues = check_text(_listing(title="블루투스 이어폰 摆拍"), PLATFORMS["shopee"].rules,
                        lang_code="ko", language="韩语")
    assert any(i.level == "error" and i.field == "title" for i in issues)


def test_japanese_legitimately_uses_kanji():
    assert not find_foreign_chars("ワイヤレスイヤホン 静音設計", "ja")
    assert find_chinese_runs("ワイヤレスイヤホン 静音設計") == ""


def test_japanese_sliding_into_chinese_is_caught():
    assert find_chinese_runs("这个产品非常好用，音质清晰持久续航能力强")
