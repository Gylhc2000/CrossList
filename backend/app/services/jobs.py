"""任务管理：创建任务、驱动 LangGraph、SSE 事件、产物打包"""
from __future__ import annotations

import asyncio
import io
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
from app.services.storage import Storage


@dataclass
class Job:
    id: str
    params: dict
    emitter: StepEmitter
    storage: Storage
    status: str = "queued"
    result: dict = field(default_factory=dict)
    error: str | None = None
    task: asyncio.Task | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None


class JobManager:
    def __init__(self, settings: Settings, llm: LlmClient, storage: Storage):
        self.settings = settings
        self.llm = llm
        self.storage = storage
        self.jobs: dict[str, Job] = {}

    # ---------------- 生命周期 ----------------
    def create(self, params: dict) -> Job:
        job_id = "job_" + uuid.uuid4().hex[:12]
        job = Job(id=job_id, params=params, emitter=StepEmitter(job_id), storage=self.storage)
        self.jobs[job_id] = job
        job.task = asyncio.create_task(self._run(job))
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    async def _run(self, job: Job) -> None:
        job.status = "running"
        job.started_at = time.time()
        state = new_state(job.id, job.params, job.emitter, self.llm, self.storage)
        await job.emitter.emit("start", steps=job.emitter.snapshot_steps())
        try:
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
            job.status = "cancelled"
            job.finished_at = time.time()
            await job.emitter.fail("任务已取消")
        except Exception as e:
            job.status = "error"
            job.error = str(e)
            job.finished_at = time.time()
            await job.emitter.fail(str(e))

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or not job.task:
            return False
        job.emitter.finished = True   # 通知条件边尽早退出（通过 state 也有一层判断）
        job.task.cancel()
        return True

    # ---------------- 快照 ----------------
    def snapshot(self, job: Job) -> dict:
        return {
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

    # ---------------- 产物 ----------------
    def files_of(self, job_id: str) -> list[dict]:
        """按平台列出产物文件"""
        out: list[dict] = []
        job = self.get(job_id)
        if not job:
            return out
        root = self.storage.job_dir(job_id)
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            files: list[dict] = []
            for f in sorted(d.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(d).as_posix()
                    if rel.startswith("images/"):
                        continue
                    files.append({"name": rel, "size": f.stat().st_size, "type": _guess_type(rel)})
            n_img_files = (
                [f for f in (d / "images").glob("*") if f.is_file()]
                if (d / "images").exists()
                else []
            )
            if n_img_files:
                files.append(
                    {
                        "name": f"images/（{len(n_img_files)} 张）",
                        "size": sum(f.stat().st_size for f in n_img_files),
                        "type": "images",
                    }
                )
            if files:
                out.append({"key": d.name, "name": d.name, "files": files})
        return out

    def zip_bytes(self, job_id: str, scope: str = "all") -> tuple[bytes, str]:
        root = self.storage.job_dir(job_id)
        if scope == "all":
            base = root
            zip_name = "CrossList_素材包_全部平台.zip"
        else:
            base = root / scope
            zip_name = f"CrossList_{scope}_素材包.zip"
        if not base.exists():
            raise FileNotFoundError(scope)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in base.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(base).as_posix())
        return buf.getvalue(), zip_name


def _guess_type(name: str) -> str:
    if name.endswith(".xlsx") or name.endswith(".csv"):
        return "template"
    if name.endswith(".md"):
        return "report"
    return "listing"
