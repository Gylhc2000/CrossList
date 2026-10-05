"""产物清理：定时删除过期任务目录 + 淘汰内存中的旧任务记录

策略（TTL 默认 24 小时）：
  - 孤儿目录（JobManager 已无记录，多为重启遗留）：目录 mtime 超过 TTL 即删除
  - 已结束任务（done/error/cancelled）：finished_at 超过 TTL → 删目录 + 淘汰内存记录
  - 运行中/排队中的任务永不触碰

删除范围严格限定为 output/ 下的 job_* 目录，其余文件一律不动。

目录扫描与 rmtree 是阻塞 IO（一个上百 MB 的任务目录能删好几秒），一律放线程池执行，
否则事件循环在这几秒里没法推 SSE。对内存任务表的读写则留在事件循环里做：
线程里改同一个字典会和 create() 撞上。
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

from app.services.jobs import TERMINAL_STATUS

logger = logging.getLogger(__name__)


def _expired(ts: float | None, ttl_seconds: float, now: float) -> bool:
    if not ts:
        return False
    return now - ts > ttl_seconds


def _snapshot(jobs: dict) -> dict[str, tuple[str, float | None]]:
    """把任务表压成 {job_id: (状态, 结束时间)}，供线程内判断使用。"""
    return {jid: (j.status, j.finished_at) for jid, j in jobs.items()}


def sweep_expired(root: Path, snapshot: dict[str, tuple], ttl_seconds: float) -> tuple[int, list[str]]:
    """删除过期任务目录，返回 (删除目录数, 待淘汰的任务 id)。

    阻塞 IO，必须由线程池调用；只读 snapshot，不碰 JobManager 的实时任务表。
    """
    now = time.time()
    removed = 0
    evict: list[str] = []
    if not root.exists():
        return 0, evict

    for d in list(root.iterdir()):
        if not d.is_dir() or not d.name.startswith("job_"):
            continue
        state = snapshot.get(d.name)
        try:
            if state is None:
                # 孤儿目录：按目录最后修改时间判断
                if _expired(d.stat().st_mtime, ttl_seconds, now):
                    shutil.rmtree(d, ignore_errors=True)
                    removed += 1
            elif state[0] in TERMINAL_STATUS and _expired(state[1], ttl_seconds, now):
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
                evict.append(d.name)
            # 运行中/排队中的任务不动
        except OSError as e:
            logger.warning("清理 %s 失败：%s", d.name, e)
    return removed, evict


def remove_job_output(root: Path, job_id: str) -> bool:
    """删除单个任务的产物目录。只接受 output/ 下的 job_* 名字，越界一律不动。

    用于用户主动删除历史记录；阻塞 IO，调用方放线程池。
    """
    if not job_id.startswith("job_") or any(sep in job_id for sep in ("/", "\\", "..")):
        return False
    target = (Path(root).resolve() / job_id).resolve()
    if not target.is_relative_to(Path(root).resolve()):
        return False
    if not target.is_dir():
        return False
    shutil.rmtree(target, ignore_errors=True)
    return not target.exists()


async def cleanup_loop(app, interval_seconds: float, ttl_seconds: float) -> None:
    """后台循环：启动时先清一轮（回收重启遗留的孤儿目录），之后按间隔执行。

    只淘汰内存里的 Job 对象与磁盘产物；SQLite 的 jobs 记录**不在这里删**——
    生成历史要跨重启长期可见，过期的是产物而不是记录。
    """
    while True:
        try:
            removed, evict = await asyncio.to_thread(
                sweep_expired,
                app.state.settings.output_path,
                _snapshot(app.state.jobs.jobs),
                ttl_seconds,
            )
            for job_id in evict:
                app.state.jobs.jobs.pop(job_id, None)
            if removed or evict:
                logger.info("产物清理：删除 %d 个目录，淘汰 %d 条任务记录（TTL %.0fh）",
                            removed, len(evict), ttl_seconds / 3600)
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
