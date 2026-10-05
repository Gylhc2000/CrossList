"""进度推送器：Agent 节点通过它向 SSE 通道写事件"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

# 五阶段（与前端进度页一致）
STEPS: list[dict[str, str]] = [
    {"key": "parse", "title": "商品信息解析", "desc": "多模态解析实拍图与文本，生成商品知识卡片"},
    {"key": "listing", "title": "多语言 Listing 生成", "desc": "标题 / 五点描述 / 详情 / 搜索关键词"},
    {"key": "image", "title": "商品主图与详情页生成", "desc": "白底图 / 场景图 / 卖点图 / 细节图"},
    {"key": "check", "title": "多平台规则校验", "desc": "标题长度 / 主图规范 / 禁用词过滤 / 语言评分"},
    {"key": "pack", "title": "批量上传格式生成与打包", "desc": "Amazon Flat File / AliExpress CSV / Shopee CSV"},
]

S_PARSE, S_LISTING, S_IMAGE, S_CHECK, S_PACK = range(5)


class StepEmitter:
    """单个任务的事件推送器，对多个 SSE 连接做扇出。

    每个订阅者拿到自己的队列：共用一条队列时，开第二个标签页或 EventSource 自动
    重连都会和第一个连接抢同一条消息，结果两边的进度都不完整。
    """

    QUEUE_MAX = 256   # 慢客户端只丢最旧的事件，不允许队列无界增长
    REPLAY_MAX = 64   # 必须小于 QUEUE_MAX：晚到的订阅者靠它补上实时产出，否则进度页会少几份

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.steps: list[dict[str, Any]] = [
            {**s, "state": "pending", "detail": "等待中"} for s in STEPS
        ]
        self.logs: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.finished = False
        self._subs: set[asyncio.Queue] = set()
        self._replay: deque[dict[str, Any]] = deque(maxlen=self.REPLAY_MAX)

    # ---------------- 订阅 ----------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self.QUEUE_MAX)
        for ev in self._replay:
            q.put_nowait(ev)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    @staticmethod
    def _offer(q: asyncio.Queue, ev: dict[str, Any]) -> None:
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(ev)
            except asyncio.QueueEmpty:
                pass

    @staticmethod
    def _now() -> int:
        return int(time.time() * 1000)

    async def emit(self, type: str, **payload: Any) -> None:
        ev = {"type": type, "ts": self._now(), **payload}
        self._replay.append(ev)
        for q in list(self._subs):
            self._offer(q, ev)

    async def log(self, msg: str, level: str = "info") -> None:
        self.logs.append({"ts": self._now(), "msg": msg, "level": level})
        await self.emit("log", msg=msg, level=level)

    async def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)
        await self.log(msg, "warn")

    async def step(self, index: int, state: str, detail: str | None = None) -> None:
        if detail is not None:
            self.steps[index]["detail"] = detail
        self.steps[index]["state"] = state
        await self.emit("step", index=index, state=state, detail=self.steps[index]["detail"])

    async def done(self, result: dict[str, Any]) -> None:
        self.finished = True
        await self.emit("done", result=result)

    async def fail(self, error: str) -> None:
        self.finished = True
        await self.log(f"任务失败：{error}", "error")
        await self.emit("fail", error=error)

    def snapshot_steps(self) -> list[dict[str, Any]]:
        return [dict(s) for s in self.steps]
