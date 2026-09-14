"""产物清理：定时删除过期任务目录 + 淘汰内存中的旧任务记录

策略（TTL 默认 24 小时）：
  - 孤儿目录（JobManager 已无记录，多为重启遗留）：目录 mtime 超过 TTL 即删除
  - 已结束任务（done/error/cancelled）：finished_at 超过 TTL → 删目录 + 淘汰内存记录
  - 运行中/排队中的任务永不触碰

删除范围严格限定为 output/ 下的 job_* 目录，其余文件一律不动。
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _expired(ts: float | None, ttl_seconds: float, now: float) -> bool:
    if not ts:
        return False
    return now - ts > ttl_seconds


def cleanup_once(root: Path, jobs: dict, ttl_seconds: float) -> tuple[int, int]:
    """执行一轮清理，返回 (删除目录数, 淘汰任务记录数)。

    jobs 为 JobManager.jobs 字典（job_id -> Job）。
    """
    now = time.time()
    removed_dirs = 0
    evicted = 0
    if not root.exists():
        return 0, 0

    for d in list(root.iterdir()):
        if not d.is_dir() or not d.name.startswith("job_"):
            continue
        job = jobs.get(d.name)
        try:
            if job is None:
                # 孤儿目录：按目录最后修改时间判断
                if _expired(d.stat().st_mtime, ttl_seconds, now):
                    shutil.rmtree(d, ignore_errors=True)
                    removed_dirs += 1
            elif (
                job.status in ("done", "error", "cancelled")
                and _expired(job.finished_at, ttl_seconds, now)
            ):
                shutil.rmtree(d, ignore_errors=True)
                jobs.pop(d.name, None)
                removed_dirs += 1
                evicted += 1
            # 运行中/排队中的任务不动
        except OSError as e:
            logger.warning("清理 %s 失败：%s", d.name, e)
    return removed_dirs, evicted


async def cleanup_loop(app, interval_seconds: float, ttl_seconds: float) -> None:
    """后台循环：启动时先清一轮（回收重启遗留的孤儿目录），之后按间隔执行。"""
    while True:
        try:
            r, e = cleanup_once(app.state.settings.output_path, app.state.jobs.jobs, ttl_seconds)
            if r or e:
                logger.info("产物清理：删除 %d 个目录，淘汰 %d 条任务记录（TTL %.0fh）",
                            r, e, ttl_seconds / 3600)
        except Exception as ex:  # 清理失败不影响主流程
            logger.warning("产物清理异常：%s", ex)
        await asyncio.sleep(interval_seconds)


def start_cleanup_task(app) -> asyncio.Task:
    settings = app.state.settings
    if settings.cleanup_ttl_hours <= 0:
        # TTL<=0 视为禁用清理
        async def _noop() -> None:
            return None

        return asyncio.create_task(_noop())
    return asyncio.create_task(
        cleanup_loop(
            app,
            interval_seconds=max(60.0, settings.cleanup_interval_minutes * 60),
            ttl_seconds=settings.cleanup_ttl_hours * 3600,
        )
    )
