"""进度推送器：Agent 节点通过它向 SSE 通道写事件"""
from __future__ import annotations

import asyncio
import time
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
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.steps: list[dict[str, Any]] = [
            {**s, "state": "pending", "detail": "等待中"} for s in STEPS
        ]
        self.logs: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.finished = False

    @staticmethod
    def _now() -> int:
        return int(time.time() * 1000)

    async def emit(self, type: str, **payload: Any) -> None:
        await self.queue.put({"type": type, "ts": self._now(), **payload})

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
