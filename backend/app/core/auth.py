"""身份：口令散列、会话令牌、登录限速、FastAPI 鉴权依赖

只用标准库（hashlib.scrypt + secrets + hmac），不引入 passlib/jose/itsdangerous。

会话是数据库里的随机令牌（存 SHA-256，明文令牌只待在浏览器 Cookie 里），
因此不需要签名密钥；数据库被拖走也还原不出可用的会话。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request

SESSION_COOKIE = "cl_session"
PASSWORD_MIN = 8
PASSWORD_MAX = 128
# scrypt 参数：n=2^14 单次约 16MB / 数十毫秒，够用且不至于把登录变成 DoS 面
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1
# 用户不存在时也要跑一次等量散列，否则响应时间直接泄露"这个用户名有没有"
_DUMMY_HASH = ""

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fff]{2,32}$")


@dataclass(frozen=True)
class User:
    id: int
    username: str
    is_admin: bool

    @classmethod
    def from_row(cls, row: Any) -> "User":
        return cls(int(row["id"]), str(row["username"]), bool(row["is_admin"]))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
                        p=_SCRYPT_P, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p),
                            maxmem=64 * 1024 * 1024, dklen=len(bytes.fromhex(hash_hex)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


def init_dummy_hash() -> None:
    global _DUMMY_HASH
    if not _DUMMY_HASH:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def new_session_token() -> tuple[str, str]:
    """返回 (明文令牌, 落库用的哈希)。"""
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def check_password_policy(username: str, password: str) -> None:
    if len(password) < PASSWORD_MIN or len(password) > PASSWORD_MAX:
        raise HTTPException(400, f"口令长度需在 {PASSWORD_MIN}~{PASSWORD_MAX} 位之间")
    if username and username.lower() in password.lower():
        raise HTTPException(400, "口令不能包含用户名")


def check_username(username: str) -> str:
    name = (username or "").strip()
    if not USERNAME_RE.match(name):
        raise HTTPException(400, "用户名只能是 2-32 位中文、字母、数字或 . _ -")
    return name


# ---------------- 登录限速 ----------------
def client_ip(request: Request, trusted_proxies: tuple[str, ...] = ()) -> str:
    """取真实来源 IP，用于限速计数。

    直连时就是 peer 地址；经 nginx 时 X-Forwarded-For 是客户端地址，
    但**只有直连对端本身也是可信代理才能信它** —— 否则任何人都能靠伪造
    `X-Forwarded-For` 把头一天内第 9 次失败算到别人 IP 上，把限速变成"给别人上锁"。
    """
    peer = request.client.host if request.client else ""
    if not peer or peer not in trusted_proxies:
        return peer or "?"
    xff = request.headers.get("x-forwarded-for", "")
    if not xff:
        return peer
    # XFF 形如 "client, 中间代理…"，最左是原始客户端
    first = xff.split(",")[0].strip()
    return first or peer


class Throttle:
    """按 (来源IP, 身份) 记失败次数。

    进程内存够用：本服务本来就是单 worker（任务态与 SSE 都在内存里），
    重启清空不影响安全语义。
    """

    def __init__(self, max_fails: int = 8, window: float = 900, lockout: float = 900):
        self.max_fails, self.window, self.lockout = max_fails, window, lockout
        self._hits: dict[str, tuple[list[float], float]] = {}

    @staticmethod
    def _key(request: Request, identity: str, trusted: tuple[str, ...] = ()) -> str:
        return f"{client_ip(request, trusted)}|{(identity or '').lower()}"

    def blocked_for(self, request: Request, identity: str,
                    trusted: tuple[str, ...] = ()) -> float:
        fails, locked_until = self._hits.get(self._key(request, identity, trusted), ([], 0.0))
        now = time.time()
        if locked_until > now:
            return locked_until - now
        return 0.0

    def fail(self, request: Request, identity: str, trusted: tuple[str, ...] = ()) -> None:
        key = self._key(request, identity, trusted)
        now = time.time()
        fails, _ = self._hits.get(key, ([], 0.0))
        fails = [t for t in fails if now - t < self.window] + [now]
        locked = now + self.lockout if len(fails) >= self.max_fails else 0.0
        self._hits[key] = (fails, locked)
        if len(self._hits) > 4096:          # 防止被拿随机用户名撑爆内存
            self._hits = {k: v for k, v in self._hits.items() if v[0] and now - v[0][-1] < self.window}

    def clear(self, request: Request, identity: str, trusted: tuple[str, ...] = ()) -> None:
        self._hits.pop(self._key(request, identity, trusted), None)


login_throttle = Throttle()
signup_throttle = Throttle(max_fails=20, window=3600, lockout=3600)


# ---------------- 依赖 ----------------
def read_user(request: Request) -> User | None:
    """从 Cookie 解析当前用户；未登录返回 None。

    同步且会写库（滑动续期），所以只能跑在线程里 —— 见 current_user。
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    db = getattr(request.app.state, "db", None)
    if db is None:
        return None
    row = db.session_user(hashlib.sha256(token.encode()).hexdigest())
    return User.from_row(row) if row else None


async def current_user(request: Request) -> User | None:
    return await asyncio.to_thread(read_user, request)


async def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(401, "请先登录")
    return user


async def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return user


def dummy_verify(password: str) -> None:
    """用户不存在时也跑一次等量散列，避免用响应时间枚举账号是否存在。"""
    verify_password(password, _DUMMY_HASH or hash_password("timing-equalizer"))


def today_start() -> float:
    """本自然日 0 点（配额按本地日切）。"""
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
