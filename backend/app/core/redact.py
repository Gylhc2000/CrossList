"""对外错误文案脱敏

上游异常字符串会经 `job.error` 和 SSE 的 fail 事件送到浏览器。里面常带模型网关
主机名、绝对路径、偶发的密钥片段 —— 那是实现细节，不是用户能用来行动的信息。
保留可读部分（HTTP 状态、错误码、中文说明），去掉地址与凭据形态的东西。
"""
from __future__ import annotations

import re

_URL = re.compile(r"(?:https?|wss?|ftp)://[^\s'\"<>,;]+")
_WIN_PATH = re.compile(r"[A-Za-z]:\\[^\s'\"<>]*")
_UNIX_PATH = re.compile(r"(?<!\w)/(?:opt|home|var|usr|etc|srv|Users)/[^\s'\"<>]*")
_CRED = re.compile(r"\b(?:sk|ak|api[-_]?key|token|bearer)[-_=: ]*[A-Za-z0-9+/]{8,}", re.I)

LIMIT = 240


def redact(text: object, limit: int = LIMIT) -> str:
    """把异常文案收敛成可安全外发的短句。"""
    s = str(text or "").strip()
    if not s:
        return "未知错误"
    s = _CRED.sub("<已隐去>", s)
    s = _URL.sub("<服务地址>", s)
    s = _WIN_PATH.sub("<路径>", s)
    s = _UNIX_PATH.sub("<路径>", s)
    s = " ".join(s.split())              # 折叠上游报文里的换行与缩进
    return s[:limit] + "…" if len(s) > limit else s
