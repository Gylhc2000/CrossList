"""CrossList AI 后端入口 —— FastAPI + LangGraph

对齐方案 4.4 技术栈：
  - Agent 框架：LangGraph（Plan-and-Execute）
  - 后端：Python + FastAPI
  - 格式生成：openpyxl（Amazon xlsx）/ pandas（CSV）
  - 存储：阿里云 OSS（未配置时回落本地 output/）
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.config_api import router as config_router
from app.api.jobs import router as jobs_router
from app.api.meta import router as meta_router
from app.core.config import get_settings
from app.core.llm import LlmClient
from app.services.cleanup import start_cleanup_task
from app.services.jobs import JobManager
from app.services.storage import Storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动产物定时清理（TTL/间隔见 Settings；cleanup_ttl_hours=0 可禁用）
    cleaner = start_cleanup_task(app)
    yield
    cleaner.cancel()
    # 释放 LlmClient 复用的连接池
    await app.state.llm.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="跨境智上Agent API",
        version="0.2.0",
        description="CrossList AI · 一键多平台商品上架素材 Agent",
        lifespan=lifespan,
    )

    settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def init_runtime() -> None:
        """初始化（或配置变更后重建）运行期对象"""
        st = get_settings()
        old_llm = getattr(app.state, "llm", None)
        llm = LlmClient(st)
        storage = Storage(st)
        app.state.settings = st
        app.state.llm = llm
        app.state.storage = storage
        app.state.jobs = JobManager(st, llm, storage)
        # 配置变更重建时，异步关闭旧实例的连接池
        # （首次初始化发生在 import 期、无运行中事件循环，此时旧实例必然为 None）
        if old_llm is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                loop.create_task(old_llm.aclose())

    app.state.reload = init_runtime
    init_runtime()

    app.include_router(meta_router)
    app.include_router(config_router)
    app.include_router(jobs_router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    print("")
    print("  跨境智上Agent（CrossList AI）后端已启动")
    print(f"  ➜  API 地址：    http://localhost:{s.backend_port}")
    print(f"  ➜  接口文档：    http://localhost:{s.backend_port}/docs")
    print(f"  ➜  模型接口：    {s.llm_base_url}/chat/completions")
    print(f"  ➜  API Key：     {'已配置' if s.llm_api_key else '未配置，请在前端「⚙ 模型配置」中填写'}")
    print("")
    uvicorn.run(app, host="0.0.0.0", port=s.backend_port, log_level="info")
