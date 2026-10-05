"""任务管理：创建任务、驱动 LangGraph、SSE 事件、产物打包"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.agent.emitter import StepEmitter
from app.agent.graph import run_agent
from app.agent.state import new_state
from app.core.config import Settings
from app.core.llm import LlmClient
from app.core.redact import redact
from app.rules.platforms import MARKET_BY_KEY, PLATFORMS
from app.services.storage import Storage

logger = logging.getLogger(__name__)

# 任务终态：进入后不再产生事件，SSE 收尾与产物清理都据此判断
TERMINAL_STATUS = ("done", "error", "cancelled")


# 出图节点把模型原始图落在任务根目录的这个子目录里（见 agent/nodes/images.py），
# 交付用的是各平台目录下已按平台尺寸适配的那一份。
STAGING_IMAGE_DIR = "images"


class JobQueueFull(RuntimeError):
    """排队 + 运行中的任务已达上限，新任务应当被拒绝"""


class QuotaExceeded(RuntimeError):
    """该账号当日任务额度已用完"""


class ZipTooLarge(RuntimeError):
    """产物体积超出打包上限"""


def _summary(params: dict) -> dict:
    """进历史列表的摘要：只放展示要的维度，不碰产物内容。"""
    pm = params.get("platform_markets") or {}
    plats = [PLATFORMS[p].name for p in pm if p in PLATFORMS]
    markets = {MARKET_BY_KEY[m].label for mks in pm.values() for m in mks if m in MARKET_BY_KEY}
    return {
        "jobCount": sum(len(v or []) for v in pm.values()),
        "platformNames": plats,
        "marketLabels": sorted(markets),
        "withImages": bool(params.get("with_images")),
    }


@dataclass
class Job:
    id: str
    params: dict
    emitter: StepEmitter
    storage: Storage
    status: str = "queued"
    result: dict = field(default_factory=dict)
    error: str | None = None
    # 上游异常原文，只给管理员（见 snapshot 的 full 参数）；对外一律用脱敏后的 error
    error_detail: str | None = None
    task: asyncio.Task | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    user_id: int | None = None      # 归属账号；None 只应出现在内部脚本里


class JobManager:
    def __init__(self, settings: Settings, llm: LlmClient, storage: Storage, db=None):
        self.settings = settings
        self.llm = llm
        self.storage = storage
        # 单例注入：配置热重载会重建本管理器，但账号/历史绝不能跟着重建
        self.db = db
        self.jobs: dict[str, Job] = {}
        # 并发上限：超出的任务排队（状态保持 queued），避免多任务 LLM 调用叠加触发限流
        self._sem = asyncio.Semaphore(max(1, settings.job_max_concurrency))

    # ---------------- 生命周期 ----------------
    async def create(self, params: dict, user=None) -> Job:
        """建任务：先过「当日额度」和「并发总数」两道闸，再落库登记归属。

        额度按用户计（模型 Key 是共享的，不限就会被单个账号烧光），
        总数按进程计（排队任务同样占内存，含 base64 实拍图）。
        """
        if user is not None and self.db is not None:
            from app.core.auth import today_start

            used = await asyncio.to_thread(self.db.count_jobs_since, user.id, today_start())
            limit = self.settings.user_daily_job_limit
            if used >= limit:
                raise QuotaExceeded(
                    f"今日已创建 {used} 个任务，达上限 {limit}；次日 0 点自动恢复"
                )
        live = sum(1 for j in self.jobs.values() if j.status not in TERMINAL_STATUS)
        if live >= self.settings.job_max_live:
            raise JobQueueFull(
                f"当前已有 {live} 个任务在排队或执行，上限 {self.settings.job_max_live}，请稍后再试"
            )
        job_id = "job_" + uuid.uuid4().hex[:12]
        job = Job(id=job_id, params=params, emitter=StepEmitter(job_id),
                  storage=self.storage, user_id=getattr(user, "id", None))
        self.jobs[job_id] = job
        if self.db is not None and job.user_id is not None:
            await asyncio.to_thread(
                self.db.insert_job, job_id, job.user_id,
                str(params.get("product_name") or ""), _summary(params),
            )
        job.task = asyncio.create_task(self._run(job))
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def forget(self, job_id: str, reason: str = "任务已删除") -> None:
        """把一条记录整体从内存里抹掉（用户删除历史时用）。

        只删 SQLite 不动这里的话，被删掉的 jobId 仍然能取到快照、仍能挂 SSE ——
        历史记录里已经看不到的任务，换个 URL 却还能翻出来，归属没漏但语义不一致。
        先标终态再摘除：正在连着的事件流会收到 fail 并自行收尾，不会悬着。
        """
        job = self.jobs.get(job_id)
        if job is None:
            return
        if job.status not in TERMINAL_STATUS:
            job.emitter.finished = True
            if job.task and not job.task.done():
                job.task.cancel()
            job.status = "cancelled"
            job.finished_at = time.time()
        self.jobs.pop(job_id, None)
        logger.info("已从内存移除任务 %s（%s）", job_id, reason)

    async def _run(self, job: Job) -> None:
        acquired = False
        try:
            # 排队等待并发槽位：期间状态保持 queued（前端会正常显示等待中）
            await self._sem.acquire()
            acquired = True
            # 排队期间可能已被取消：直接收敛，不再执行
            if job.emitter.finished:
                job.status = "cancelled"
                job.finished_at = time.time()
                await job.emitter.fail("任务已取消")
                return
            job.status = "running"
            job.started_at = time.time()
            state = new_state(job.id, job.params, job.emitter, self.llm, self.storage)
            await job.emitter.emit("start", steps=job.emitter.snapshot_steps())
            final = await run_agent(state)
            job.result = {
                "plan": final.get("plan") or {},
                "knowledge_card": final.get("knowledge_card") or {},
                "listings": final.get("listings") or {},
                "images": final.get("images") or [],
                "platforms": final.get("platforms") or [],
                "artifacts": final.get("artifacts") or {},
                "report": final.get("report") or {},
            }
            job.status = "done"
            job.finished_at = time.time()
            await job.emitter.done(job.result)
        except asyncio.CancelledError:
            # 含排队中被取消的情况：必须收敛状态并推送 fail，
            # 否则前端看门狗会一直看到 queued、永远无法结束
            job.status = "cancelled"
            job.finished_at = time.time()
            await job.emitter.fail("任务已取消")
        except Exception as e:
            job.status = "error"
            # 上游异常原文只进日志；对外给脱敏后的短句（见 core.redact）
            job.error = redact(e)
            job.error_detail = str(e)[:2000]
            logger.exception("任务 %s 执行失败", job.id)
            job.finished_at = time.time()
            await job.emitter.fail(job.error)
        finally:
            if acquired:
                self._sem.release()
            # 先把终态写进历史索引，再释放内存 —— 顺序反了会让"记录已落库"
            # 依赖后面几步不抛异常
            if self.db is not None and job.user_id is not None:
                try:
                    await asyncio.to_thread(
                        self.db.finish_job, job.id, job.status, job.error, job.result or None)
                except Exception as e:      # 落库失败不能把已完成的任务报成失败
                    logger.warning("任务 %s 终态落库失败：%s", job.id, e)
            # 任务已终结，实拍图的 base64 再没人读（没有重跑入口），
            # 但任务记录要在内存里留满 TTL 供回看，单张可达数 MB 的图片必须先释放
            job.params.pop("images", None)

    async def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or not job.task or job.task.done():
            return False
        job.emitter.finished = True   # 标记已结束，_run 拿到槽位时据此直接收敛
        job.task.cancel()
        # 兜底：task 尚未首次调度时，CancelledError 不会进入协程体，
        # 状态会永远停在 queued。这里同步收敛（前端对 cancelled 走快照收敛，不依赖 fail 事件）
        # 这条路径上 _run 的 finally 也不会执行，所以终态必须由这里补写，
        # 否则历史列表里会留下一条永远"排队中"的记录
        if job.status == "queued":
            job.status = "cancelled"
            job.finished_at = time.time()
            if self.db is not None and job.user_id is not None:
                await asyncio.to_thread(
                    self.db.finish_job, job.id, job.status, None, None)
        return True

    # ---------------- 快照 ----------------
    def snapshot(self, job: Job, full: bool = False) -> dict:
        """full=True 才带上游异常原文；普通用户拿到的 error 已脱敏。"""
        snap = {
            "id": job.id,
            "status": job.status,
            "steps": job.emitter.snapshot_steps(),
            "logs": job.emitter.logs[-200:],
            "warnings": job.emitter.warnings,
            "result": job.result,
            "error": job.error,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
        }
        if full:
            snap["error_detail"] = job.error_detail
        return snap

    # ---------------- 产物 ----------------
    def files_of(self, job_id: str) -> list[dict]:
        """按平台列出产物文件。阻塞 IO，由接口层放进线程池调用。

        不要求任务还在内存里：重启后的冷记录只要产物未过 TTL 就仍然可看可下载，
        归属由接口层的 _owned_job 把关。
        """
        out: list[dict] = []
        root = self.storage.job_path(job_id)
        try:
            entries = sorted(root.iterdir())
        except OSError:
            return out
        for d in entries:
            if not d.is_dir():
                continue
            # 任务根目录下的 images/ 是出图节点的**中转目录**（模型原始尺寸），
            # 交付用的是各平台目录里已适配好的那一份。列进来会让卖家以为有两个版本的图，
            # 还会让"全部平台"的体积比任何一个平台卡都大得莫名其妙
            if d.name == STAGING_IMAGE_DIR:
                continue
            files: list[dict] = []
            for f in _walk(d):
                size = _size_of(f)
                if size is None:
                    continue
                rel = f.relative_to(d).as_posix()
                if rel.startswith("images/"):
                    continue
                files.append({"name": rel, "size": size, "type": _guess_type(rel)})
            img_sizes = [_size_of(f) for f in sorted((d / "images").glob("*"))]
            img_sizes = [s for s in img_sizes if s is not None]
            if img_sizes:
                files.append(
                    {
                        "name": f"images/（{len(img_sizes)} 张）",
                        "size": sum(img_sizes),
                        "type": "images",
                    }
                )
            if files:
                out.append({"key": d.name, "name": d.name, "files": files})
        return out

    def zip_to_tempfile(self, job_id: str, scope: str = "all") -> tuple[Path, str]:
        """把产物打包到临时文件，返回 (临时文件路径, 下载文件名)。

        不返回字节而是落盘：4 平台 × 9 张 2000px PNG 就有上百 MB，
        全量装进 BytesIO 再整体响应，几个并发下载能把进程内存吃光。
        调用方（接口层）负责用完删除临时文件；本方法内部的异常路径已自行清理。
        阻塞 IO，必须由 asyncio.to_thread 之类的方式调。
        """
        root = self.storage.job_path(job_id).resolve()
        if scope == "all":
            base = root
            zip_name = "CrossList_素材包_全部平台.zip"
        else:
            # scope 来自查询参数，只允许任务目录下的一级平台目录：
            # 否则 ../.. 或绝对路径会把 .env（模型与 OSS 密钥）整个打包送回浏览器
            base = self.storage.resolve_in_job(job_id, scope)
            if base is None or base.parent != root or not base.is_dir():
                raise FileNotFoundError(scope)
            zip_name = f"CrossList_{base.name}_素材包.zip"

        limit = self.settings.zip_max_mb * 1024 * 1024
        fd, tmp = tempfile.mkstemp(prefix="crosslist_", suffix=".zip")
        total = 0
        written = 0
        try:
            with os.fdopen(fd, "wb") as fh, zipfile.ZipFile(fh, "w", zipfile.ZIP_DEFLATED) as z:
                for f in _walk(base):
                    size = _size_of(f)
                    if size is None:
                        continue
                    rel = f.relative_to(base).as_posix()
                    # 全量包里排除出图中转目录：同一张图已经有按平台尺寸适配好的版本，
                    # 原图再塞一份等于每张图发两遍（9 张 2000px 就多 8MB）
                    if scope == "all" and rel.startswith(f"{STAGING_IMAGE_DIR}/"):
                        continue
                    total += size
                    if total > limit:
                        raise ZipTooLarge(
                            f"产物体积超过 {self.settings.zip_max_mb}MB 上限，请改为按平台分别下载"
                        )
                    try:
                        z.write(f, rel)
                        written += 1
                    except OSError:
                        # 清理线程刚删掉该文件：跳过这一项比整次下载报错更可用
                        continue
            if written == 0:
                # 产物目录已被 TTL 清掉或用户已删除：此时发一个 22 字节的空 zip，
                # 浏览器会下载一个连不上任何内容的包，比直接 404 更让人摸不着头脑
                raise FileNotFoundError(scope)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return Path(tmp), zip_name


def _size_of(f: Path) -> int | None:
    """文件大小；文件不存在或刚被清理线程删掉时返回 None，由调用方跳过。"""
    try:
        return f.stat().st_size if f.is_file() else None
    except OSError:
        return None


def _walk(base: Path) -> list[Path]:
    """列出目录下全部条目。清理任务跑在别的线程里，目录可能正被删，此时返回空。"""
    try:
        return sorted(base.rglob("*"))
    except OSError:
        return []


def _guess_type(name: str) -> str:
    if name.endswith(".zip"):
        return "archive"
    if "对照表" in name:
        return "worksheet"
    if name.endswith(".xlsx") or name.endswith(".csv"):
        return "template"
    if name.endswith(".md"):
        return "report"
    return "listing"
