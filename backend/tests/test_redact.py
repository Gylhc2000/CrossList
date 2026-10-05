"""对外错误文案脱敏

`job.error` 与 SSE 的 fail 事件会原样送到浏览器，上游报文里常带网关主机名、
绝对路径、偶发的密钥片段。这里钉住的是"这些不许出去"，不是具体文案。
"""
from app.core.redact import LIMIT, redact


def test_empty_input_becomes_generic_phrase():
    assert redact("") == "未知错误"
    assert redact(None) == "未知错误"


def test_service_urls_are_removed():
    out = redact("upstream failed: https://gw.internal-tenant.example.com/v1/chat 502")
    assert "internal-tenant" not in out and "example.com" not in out
    assert "502" in out                      # 可行动的信息要留下


def test_windows_and_unix_paths_are_removed():
    assert "bob" not in redact(r"cannot open C:\Users\bob\.env")
    assert "/etc" not in redact("/etc/passwd unreadable")


def test_credential_looking_tokens_are_masked():
    # 掩码规则认的是 "凭据前缀 + 分隔符 + ≥8 位字母数字" 这一形态
    assert "abcdef123456" not in redact("apiKey=sk-abcdef123456 rejected")
    assert "Abc123456789" not in redact("token=Abc123456789 rejected")


def test_whitespace_is_folded_and_length_capped():
    assert redact("line one\n      line two") == "line one line two"
    long = "x" * (LIMIT * 3)
    out = redact(long)
    assert len(out) <= LIMIT + 1 and out.endswith("…")


def test_plain_chinese_message_survives():
    msg = "模型网关返回 429，已重试 3 次仍失败"
    assert redact(msg) == msg
