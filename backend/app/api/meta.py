"""元信息接口：模型清单 / 市场 / 平台规则 / Agent 图结构"""
from __future__ import annotations

import time

from fastapi import APIRouter

from app.agent.emitter import STEPS
from app.agent.graph import graph_mermaid
from app.core.config import (
    AUDIO_MODELS,
    IMAGE_MODELS,
    TEXT_MODELS,
    Settings,
    get_settings,
)
from app.rules.platforms import MARKETS, PLATFORMS

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/meta")
async def meta():
    s: Settings = get_settings()
    return {
        "baseUrl": s.llm_base_url,
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
                "rules": {
                    "titleMax": p.rules.title_max, "bulletMax": p.rules.bullet_max,
                    "bulletCount": p.rules.bullet_count, "keywordMax": p.rules.keyword_max,
                    "descMax": p.rules.desc_max, "mainImage": p.rules.main_image,
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
