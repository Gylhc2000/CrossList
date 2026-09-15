"""任务接口：创建 / 快照 / SSE 进度 / 取消 / 素材 / 打包下载"""
from __future__ import annotations

import asyncio
import json
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.rules.platforms import MARKET_BY_KEY, PLATFORMS

router = APIRouter(prefix="/api", tags=["jobs"])


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
async def create_job(body: JobCreate, request: Request):
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
    for pk in platforms:
        mks = [m for m in (body.platformMarkets.get(pk) or []) if m in MARKET_BY_KEY]
        if not mks:
            mks = [PLATFORMS[pk].default_market]
        # 去重且保持用户勾选顺序
        seen: set[str] = set()
        platform_markets[pk] = [m for m in mks if not (m in seen or seen.add(m))]

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
    }
    job = _mgr(request).create(params)
    return {"jobId": job.id}


@router.get("/jobs/{job_id}")
async def job_snapshot(job_id: str, request: Request):
    job = _mgr(request).get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return _mgr(request).snapshot(job)


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request):
    mgr = _mgr(request)
    job = mgr.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")

    async def gen():
        yield _sse({"type": "hello", "job": mgr.snapshot(job)})
        if job.status in ("done", "error", "cancelled"):
            yield _sse({"type": "end", "status": job.status})
            return
        while True:
            try:
                ev = await asyncio.wait_for(job.emitter.queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
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
async def cancel_job(job_id: str, request: Request):
    ok = _mgr(request).cancel(job_id)
    return {"ok": ok}


@router.get("/jobs/{job_id}/files")
async def job_files(job_id: str, request: Request):
    mgr = _mgr(request)
    if not mgr.get(job_id):
        raise HTTPException(404, "任务不存在")
    return {"platforms": mgr.files_of(job_id)}


@router.get("/jobs/{job_id}/asset")
async def job_asset(job_id: str, p: str, request: Request):
    mgr = _mgr(request)
    job = mgr.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    root = mgr.storage.job_dir(job_id).resolve()
    target = (root / p.replace("..", "")).resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        raise HTTPException(404, "文件不存在")
    data = target.read_bytes()
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
async def job_download(job_id: str, request: Request, scope: str = "all"):
    mgr = _mgr(request)
    if not mgr.get(job_id):
        raise HTTPException(404, "任务不存在")
    try:
        data, zip_name = mgr.zip_bytes(job_id, scope)
    except FileNotFoundError:
        raise HTTPException(404, "产物不存在")
    from urllib.parse import quote

    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(zip_name)}",
            "Content-Length": str(len(data)),
        },
    )
