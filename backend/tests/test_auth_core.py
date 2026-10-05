"""口令散列、会话令牌、用户名/口令策略、来源 IP 与登录限速

限速这块的两条性质是安全边界，值得钉住：
直连时对端不是可信代理就不许信 X-Forwarded-For（否则攻击者能自选计数用的 IP，
把别人锁上），以及经反代时才按真实客户端分桶。
"""
import hashlib

import pytest
from fastapi import HTTPException

from app.core.auth import (
    PASSWORD_MAX,
    PASSWORD_MIN,
    Throttle,
    check_password_policy,
    check_username,
    client_ip,
    hash_password,
    new_session_token,
    verify_password,
)


class _Client:
    def __init__(self, host):
        self.host = host


class _Req:
    """最小可用的 Request 替身：client_ip 只读 client.host 与一个 header"""

    def __init__(self, host="203.0.113.7", xff=None):
        self.client = _Client(host) if host else None
        self.headers = {"x-forwarded-for": xff} if xff else {}


# ---------------- 口令散列 ----------------
def test_password_roundtrip():
    stored = hash_password("Sup3rSecret-Pass")
    assert verify_password("Sup3rSecret-Pass", stored)
    assert not verify_password("Sup3rSecret-Pas", stored)


def test_same_password_hashes_differently_each_time():
    assert hash_password("same-pass-word") != hash_password("same-pass-word")


def test_malformed_stored_hash_is_rejected_not_crashing():
    assert verify_password("x", "garbage") is False
    assert verify_password("x", "bcrypt$1$2$3$4$5") is False


# ---------------- 会话令牌 ----------------
def test_session_token_is_random_and_stored_hashed():
    token, digest = new_session_token()
    assert token != digest
    assert digest == hashlib.sha256(token.encode()).hexdigest()
    assert len(token) >= 32
    assert new_session_token()[0] != token


# ---------------- 用户名 / 口令策略 ----------------
@pytest.mark.parametrize("bad", ["a", "带 空 格", "", "x" * 33, "emoji🙂bad"])
def test_bad_usernames_rejected(bad):
    with pytest.raises(HTTPException):
        check_username(bad)


def test_username_is_trimmed_and_cjk_allowed():
    assert check_username("  小王_01  ") == "小王_01"


def test_password_policy_bounds_and_username_overlap():
    ok = "x" * PASSWORD_MIN
    with pytest.raises(HTTPException):
        check_password_policy("alice", "x" * (PASSWORD_MIN - 1))
    with pytest.raises(HTTPException):
        check_password_policy("alice", "x" * (PASSWORD_MAX + 1))
    with pytest.raises(HTTPException):
        check_password_policy("alice", "alice1234567")     # 含用户名
    check_password_policy("alice", ok)                     # 不抛即通过


# ---------------- 来源 IP ----------------
def test_xff_is_ignored_unless_the_peer_is_a_trusted_proxy():
    assert client_ip(_Req(host="203.0.113.7", xff="9.9.9.9"), ()) == "203.0.113.7"
    assert client_ip(_Req(host="127.0.0.1", xff="9.9.9.9"), ("127.0.0.1",)) == "9.9.9.9"


def test_only_the_leftmost_xff_entry_is_the_client():
    got = client_ip(_Req(host="10.0.0.2", xff="1.2.3.4, 10.0.0.2"), ("10.0.0.2",))
    assert got == "1.2.3.4"


def test_missing_client_socket_falls_back_to_placeholder():
    assert client_ip(_Req(host=None), ()) == "?"


# ---------------- 登录限速 ----------------
def test_lockout_after_max_fails_and_reset_on_success():
    t = Throttle(max_fails=3, window=60, lockout=60)
    req = _Req()
    for _ in range(2):
        t.fail(req, "alice")
    assert t.blocked_for(req, "alice") == 0.0
    t.fail(req, "alice")
    assert t.blocked_for(req, "alice") > 0.0
    t.clear(req, "alice")
    assert t.blocked_for(req, "alice") == 0.0


def test_buckets_are_per_identity():
    t = Throttle(max_fails=1, window=60, lockout=60)
    req = _Req()
    t.fail(req, "alice")
    assert t.blocked_for(req, "alice") > 0.0
    assert t.blocked_for(req, "bob") == 0.0


def test_forged_xff_cannot_dodge_the_lockout():
    # 直连（对端不在可信名单）时，伪造头不能把自己每次失败算到不同 IP 上
    t = Throttle(max_fails=2, window=60, lockout=60)
    t.fail(_Req(xff="1.1.1.1"), "alice")
    t.fail(_Req(xff="2.2.2.2"), "alice")
    assert t.blocked_for(_Req(xff="3.3.3.3"), "alice") > 0.0


def test_behind_trusted_proxy_different_clients_get_different_buckets():
    # Throttle 默认不信任任何代理（见上一条用例），要按客户端分桶必须显式给出可信名单
    trusted = ("127.0.0.1",)
    t = Throttle(max_fails=2, window=60, lockout=60)

    def req(xff):
        return _Req(host="127.0.0.1", xff=xff)

    t.fail(req("1.1.1.1"), "alice", trusted)
    t.fail(req("2.2.2.2"), "alice", trusted)
    assert t.blocked_for(req("3.3.3.3"), "alice", trusted) == 0.0
    # 同一个客户端继续失败仍会被锁
    t.fail(req("1.1.1.1"), "alice", trusted)
    assert t.blocked_for(req("1.1.1.1"), "alice", trusted) > 0.0
