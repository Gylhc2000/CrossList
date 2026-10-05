"""元信息接口：模型清单 / 市场 / 平台规则 / Agent 图结构

需要登录：响应里带模型服务地址与全量规则，未鉴权公开等于给外部一张说明书。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request

from app.agent.emitter import STEPS
from app.agent.graph import graph_mermaid
from app.core.auth import User, require_user
from app.core.config import (
    AUDIO_MODELS,
    IMAGE_MODELS,
    TEXT_MODELS,
    get_settings,
)
from app.rules.platforms import MARKETS, PLATFORMS

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/meta")
async def meta(request: Request, user: User = Depends(require_user)):
    return {
        # 不返回 baseUrl：模型网关地址属于实现细节（错误文案里都刻意隐去它），
        # 而前端读的是 /api/config 里那份带掩码的配置
        "textModels": TEXT_MODELS,
        "imageModels": IMAGE_MODELS,
        "audioModels": AUDIO_MODELS,
        "markets": [
            {"key": m.key, "label": m.label, "flag": m.flag,
             "language": m.language, "langCode": m.lang_code}
            for m in MARKETS
        ],
        "platforms": [
            {
                "key": p.key, "name": p.name, "imageSize": p.image_size,
                "defaultMarket": p.default_market, "fileExt": p.file_ext,
                # 该平台真实有站点的市场：前端据此禁用勾选项，
                # 否则用户能勾出「Amazon × 韩国」这种根本不存在的组合
                "markets": list(p.markets),
                "rules": {
                    "titleMax": p.rules.title_max, "bulletMax": p.rules.bullet_max,
                    "bulletCount": p.rules.bullet_count, "keywordMax": p.rules.keyword_max,
                    "keywordMaxBytes": p.rules.keyword_max_bytes,
                    "descMax": p.rules.desc_max, "mainImage": p.rules.main_image,
                    # 规则数值的核对时间：平台会改（Amazon 标题 200→75），
                    # 没有这个标记就无法区分"平台如此规定"和"我们三年没看过它"
                    "asof": p.rules.asof,
                },
            }
            for p in PLATFORMS.values()
        ],
        "steps": STEPS,
        "graph": graph_mermaid(),
        "stack": {
            "backend": "Python + FastAPI",
            "agent": "LangGraph (Plan-and-Execute)",
            "exporters": "openpyxl / pandas",
        },
    }


@router.get("/health")
async def health():
    import time

    s = get_settings()
    return {
        "ok": True,
        "hasKey": bool(s.llm_api_key),
        "model": s.llm_text_model,
        # 进程启动时间与存活时长：用于识别「改了代码但老进程仍在响应」的僵尸实例
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_PROC_START)),
        "uptime_s": int(time.time() - _PROC_START),
    }


_PROC_START = time.time()
