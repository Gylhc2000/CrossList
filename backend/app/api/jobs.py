"""任务接口：创建 / 快照 / SSE 进度 / 取消 / 素材 / 打包下载"""
from __future__ import annotations

import asyncio
import json
import mimetypes
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.core.audit import audit
from app.core.auth import User, require_user
from app.rules.platforms import MARKET_BY_KEY, PLATFORMS, market_supported
from app.services.jobs import (
    JobQueueFull,
    QuotaExceeded,
    TERMINAL_STATUS,
    ZipTooLarge,
)

router = APIRouter(prefix="/api", tags=["jobs"])


def _remove(path) -> None:
    """响应发完即删临时 zip；发送失败时 starlette 同样会执行 BackgroundTask"""
    path.unlink(missing_ok=True)


def _owned_job_sync(request: Request, job_id: str, user: User):
    """取该账号名下的内存任务；不在内存但在库里有归属则返回 None（冷记录）。

    别人的 jobId 与不存在的 jobId 都返回 404 —— 用 403 会泄露"这个任务存在"。
    """
    mgr = request.app.state.jobs
    job = mgr.get(job_id)
    if job is not None:
        if job.user_id != user.id:
            raise HTTPException(404, "任务不存在")
        return job
    db = getattr(request.app.state, "db", None)
    if db is None or db.job_owner(job_id) != user.id:
        raise HTTPException(404, "任务不存在")
    return None


async def _job_or_404(request: Request, job_id: str, user: User):
    """要求任务仍在内存里（进度/取消这类需要活对象的接口）。"""
    job = await asyncio.to_thread(_owned_job_sync, request, job_id, user)
    if job is None:
        raise HTTPException(409, "该任务已结束且不在服务内存中，请从历史记录重新查看")
    return job


async def _check_owned(request: Request, job_id: str, user: User) -> None:
    """只验归属与所有权，产物类接口用（冷记录的磁盘文件可能仍在 TTL 内）。"""
    await asyncio.to_thread(_owned_job_sync, request, job_id, user)


async def _cold_snapshot(request: Request, job_id: str, user: User) -> dict | None:
    """服务重启后内存里没有这个任务了，用库里存的终态结果重建快照。"""
    rec = await asyncio.to_thread(request.app.state.db.job_record, job_id, user.id)
    if not rec:
        return None
    result: dict = {}
    if rec.get("result_json"):
        try:
            result = json.loads(rec["result_json"]) or {}
        except ValueError:
            result = {}
    return {
        "id": job_id,
        "status": rec["status"],
        "steps": [],
        "logs": [],
        "warnings": [],
        "result": result,
        "error": rec.get("error"),
        "started_at": rec.get("created_at"),
        "finished_at": rec.get("finished_at"),
        "cold": True,     # 前端据此说明"产物可能已过期"
    }


class JobCreate(BaseModel):
    productName: str = ""
    category: str = ""
    description: str = ""
    specs: str = ""
    brand: str = ""                # 选填品牌，未填由模型推断
    price: float | None = None     # 选填参考售价，未填由模型估计
    currency: str = "USD"
    platforms: list[str] = Field(default_factory=list)
    markets: list[str] = Field(default_factory=list)
    # 每平台各自勾选的目标市场（键=平台 key，值=市场 key 列表）；
    # 产出 = Σ 每平台勾选数。与 markets 同时给出时以 platform_markets 为准
    platformMarkets: dict[str, list[str]] = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list)   # data URL 列表
    withImages: bool = True


def _sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"


def _mgr(request: Request):
    return request.app.state.jobs


NAME_MAX = 80
DESC_MAX = 1200
SPECS_MAX = 4000
CATEGORY_MAX = 40
# 图片防线（前端压缩后单张约 0.2~0.5MB，这里按 data URL 字符数限，
# base64 约为原始体积的 4/3）：单张 ≈4MB、合计 ≈12MB，拦截绕过前端的超大请求体
IMG_URL_MAX_CHARS = 5_500_000
IMGS_TOTAL_MAX_CHARS = 16_500_000


def _validate_images(images: list[str]) -> list[str]:
    total = 0
    for u in images:
        n = len(u)
        if n > IMG_URL_MAX_CHARS:
            raise HTTPException(400, "单张图片过大，请上传 4MB 以内的图片")
        total += n
        if total > IMGS_TOTAL_MAX_CHARS:
            raise HTTPException(400, "图片总量过大，合计请控制在 12MB 以内")
    return images


@router.post("/jobs")
async def create_job(body: JobCreate, request: Request, user: User = Depends(require_user)):
    # 必填校验：商品名称、目标平台、目标市场（描述为软必填，仅前端引导）
    name = body.productName.strip()
    if not name:
        raise HTTPException(400, "请填写商品名称")
    if len(name) > NAME_MAX:
        raise HTTPException(400, f"商品名称不能超过 {NAME_MAX} 字")
    desc = body.description.strip()
    if len(desc) > DESC_MAX:
        raise HTTPException(400, f"商品描述不能超过 {DESC_MAX} 字")
    specs = body.specs.strip()
    if len(specs) > SPECS_MAX:
        raise HTTPException(400, f"规格参数不能超过 {SPECS_MAX} 字")
    category = body.category.strip()
    if len(category) > CATEGORY_MAX:
        raise HTTPException(400, f"商品类目不能超过 {CATEGORY_MAX} 字")
    brand = body.brand.strip()
    if len(brand) > 40:
        raise HTTPException(400, "品牌不能超过 40 字符")
    if body.price is not None and (body.price <= 0 or body.price > 1_000_000):
        raise HTTPException(400, "参考售价需为 0 ~ 1,000,000 之间的数字")
    currency = body.currency.strip().upper() or "USD"
    if currency not in ("USD", "EUR", "GBP", "CAD", "AUD"):
        currency = "USD"
    platforms = [p for p in body.platforms if p in PLATFORMS]
    if not platforms:
        raise HTTPException(400, "请至少选择一个目标平台")

    # 每平台的市场勾选：过滤非法键，未给出的平台回退到其默认市场
    platform_markets: dict[str, list[str]] = {}
    market_notes: list[str] = []
    for pk in platforms:
        mks = [m for m in (body.platformMarkets.get(pk) or []) if m in MARKET_BY_KEY]
        if not mks:
            mks = [PLATFORMS[pk].default_market]
        # 去重且保持用户勾选顺序
        seen: set[str] = set()
        mks = [m for m in mks if not (m in seen or seen.add(m))]
        # 该平台在本市场没有自营站点：不拦，勾选权归用户。
        # 但必须留下痕迹 —— 市场键绑死语言，"Amazon×韩国"产出的是一条指向
        # 不存在站点的韩语 Listing，卖家会误以为那是个能上的渠道。
        bad = [m for m in mks if not market_supported(pk, m)]
        if bad:
            names = "、".join(MARKET_BY_KEY[m].label for m in bad)
            ok_names = "、".join(MARKET_BY_KEY[m].label for m in PLATFORMS[pk].markets)
            market_notes.append(
                f"{PLATFORMS[pk].name} × {names}：该平台无此站点"
                + (f"（其在本项目可选市场内的站点：{ok_names}）" if ok_names else "")
                + "，产物仅作素材参考，不可直接上架"
            )
        platform_markets[pk] = mks

    # 汇总所有被涉及的市场（供 market_labels 等兼容字段使用）
    markets: list[str] = []
    for mks in platform_markets.values():
        for m in mks:
            if m not in markets:
                markets.append(m)

    params = {
        "product_name": name,
        "category": category or "未分类",
        "description": desc,
        "specs": specs,
        "brand": brand,
        "price": body.price,
        "currency": currency,
        "platforms": platforms,
        "platform_markets": platform_markets,
        "markets": markets,
        "market_labels": [
            f"{MARKET_BY_KEY[m].flag}{MARKET_BY_KEY[m].label}（{MARKET_BY_KEY[m].language}）"
            for m in markets
        ],
        "images": _validate_images(body.images[:5]),
        "with_images": body.withImages,
        # 平台×市场无自营站点的组合：照常生成，但要一路带到提示与报告里
        "market_notes": market_notes,
    }
    try:
        job = await _mgr(request).create(params, user=user)
    except (JobQueueFull, QuotaExceeded) as e:
        audit("create_job", request=request, user=user, ok=False, reason=str(e)[:80])
        raise HTTPException(429, str(e))
    audit("create_job", request=request, user=user, job_id=job.id,
          units=len(params.get("platform_markets") or {}))
    return {"jobId": job.id}


@router.get("/jobs/{job_id}")
async def job_snapshot(job_id: str, request: Request, user: User = Depends(require_user)):
    job = await asyncio.to_thread(_owned_job_sync, request, job_id, user)
    if job is not None:
        return _mgr(request).snapshot(job, full=user.is_admin)
    snap = await _cold_snapshot(request, job_id, user)
    if snap is None:
        raise HTTPException(404, "任务不存在")
    return snap


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request, user: User = Depends(require_user)):
    mgr = _mgr(request)
    job = await _job_or_404(request, job_id, user)

    async def gen():
        q = job.emitter.subscribe()
        try:
            yield _sse({"type": "hello", "job": mgr.snapshot(job, full=user.is_admin)})
            if job.status in TERMINAL_STATUS:
                yield _sse({"type": "end", "status": job.status})
                return
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    # 排队中的任务被取消时协程体从未执行，队列里永远不会有 done/fail，
                    # 只靠事件收尾会让这个连接每 15 秒 ping 一次、永不结束
                    if job.status in TERMINAL_STATUS:
                        yield _sse({"type": "end", "status": job.status})
                        return
                    continue
                except asyncio.CancelledError:
                    break
                if ev.get("type") in ("done", "fail"):
                    # 把权威时间戳带进事件，前端进度页据此计算处理耗时
                    ev = {**ev, "started_at": job.started_at, "finished_at": job.finished_at}
                yield _sse(ev)
                if ev.get("type") in ("done", "fail"):
                    yield _sse({"type": "end", "status": job.status})
                    break
        finally:
            job.emitter.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request, user: User = Depends(require_user)):
    await _job_or_404(request, job_id, user)
    ok = await _mgr(request).cancel(job_id)
    return {"ok": ok}


@router.get("/jobs/{job_id}/files")
async def job_files(job_id: str, request: Request, user: User = Depends(require_user)):
    await _check_owned(request, job_id, user)
    # 目录遍历与 stat 是阻塞 IO，别把它们留在事件循环上
    return {"platforms": await asyncio.to_thread(_mgr(request).files_of, job_id)}


@router.get("/jobs/{job_id}/asset")
async def job_asset(job_id: str, p: str, request: Request,
                    user: User = Depends(require_user)):
    mgr = _mgr(request)
    await _check_owned(request, job_id, user)
    target = mgr.storage.resolve_in_job(job_id, p)
    if target is None or not target.is_file():
        raise HTTPException(404, "文件不存在")
    data = await asyncio.to_thread(target.read_bytes)
    ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    # 内容嗅探：图像接口失败时会写入 SVG 占位内容（文件名按平台规范保留 .png），
    # 按内容返回 image/svg+xml，保证浏览器能正常渲染预览
    head = data[:256].lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<svg"):
        ctype = "image/svg+xml"
    return Response(
        content=data,
        media_type=ctype,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/jobs/{job_id}/download")
async def job_download(job_id: str, request: Request, user: User = Depends(require_user),
                       scope: str = "all"):
    mgr = _mgr(request)
    await _check_owned(request, job_id, user)
    # 打包是纯 CPU + 磁盘写，且要先把整个 zip 落盘。单 worker 下不限并发，
    # 几个同时下载会把所有人的响应拖死 —— 宁可直接 429 让后来者重试。
    sem = request.app.state.download_sem
    if sem.locked():
        # locked() 只是快速拒绝，真并发度由信号量兜住（极端情况下多排一两个队无所谓）
        raise HTTPException(429, "服务器正在为其他用户打包，请稍后重试")
    async with sem:
        try:
            path, zip_name = await asyncio.to_thread(mgr.zip_to_tempfile, job_id, scope)
        except FileNotFoundError:
            raise HTTPException(404, "产物不存在")
        except ZipTooLarge as e:
            raise HTTPException(413, str(e))

    # 打包完就交还并发额度；FileResponse 分块发送并自算 Content-Length，临时文件发完即删
    return FileResponse(
        path,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(zip_name)}",
        },
        background=BackgroundTask(_remove, path),
    )
