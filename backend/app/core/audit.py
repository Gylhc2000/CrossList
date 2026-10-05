"""安全事件审计日志

只记"谁在什么时候做了什么"，不记内容 —— 商品资料、口令、API Key、模型网关的
响应体都不该进日志。单 worker + 标准 logging，经 systemd 收进 journal，
所以直接打一条可 grep 的结构化行，不引入日志框架。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from app.core.auth import User, client_ip

logger = logging.getLogger("crosslist.audit")


def audit(event: str, *, request: Request | None = None, user: User | None = None,
          ok: bool = True, job_id: str = "", **extra: Any) -> None:
    """记一条审计事件。

    走 logger.warning 而非 info：journal 默认级别下也能看到，且这些事件本来就
    属于"事后要回查"的那一类，不该被淹没在常规 INFO 里。
    """
    parts = [f"event={event}", f"ok={1 if ok else 0}"]
    if user is not None:
        parts.append(f"user={user.username}")
        parts.append(f"uid={user.id}")
    if job_id:
        parts.append(f"job={job_id}")
    if request is not None:
        settings = getattr(getattr(request.app, "state", None), "settings", None)
        trusted = settings.trusted_proxy_set if settings else ()
        ip = client_ip(request, trusted)
        peer = request.client.host if request.client else ""
        parts.append(f"ip={ip}")
        # 直连对端与"声称的客户端"不一致，说明中间有反代或有人在伪造 XFF
        if peer and peer != ip:
            parts.append(f"peer={peer}")
        parts.append(f"ua={request.headers.get('user-agent', '')[:60]}")
    for k, v in extra.items():
        if v is None or v == "":
            continue
        parts.append(f"{k}={v}")
    logger.warning("audit %s", " ".join(parts))
