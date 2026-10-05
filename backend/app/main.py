"""CrossList AI 后端入口 —— FastAPI + LangGraph

对齐方案 4.4 技术栈：
  - Agent 框架：LangGraph（Plan-and-Execute）
  - 后端：Python + FastAPI
  - 格式生成：openpyxl（Amazon xlsx）/ pandas（CSV）
  - 存储：阿里云 OSS（未配置时回落本地 output/）
  - 账号：SQLite（标准库），会话走 HttpOnly Cookie
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.auth_api import router as auth_router
from app.api.config_api import router as config_router
from app.api.jobs import router as jobs_router
from app.api.meta import router as meta_router
from app.core.auth import init_dummy_hash
from app.core.config import get_settings
from app.core.llm import LlmClient
from app.services.cleanup import start_cleanup_task
from app.services.db import Db
from app.services.jobs import JobManager
from app.services.storage import Storage

logger = logging.getLogger(__name__)

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = app.state.settings
    if not settings.llm_api_key:
        logger.warning("未配置 LLM_API_KEY，生成类接口会直接报错")
    if not settings.allow_open_signup and not settings.invite_code:
        logger.warning("注册未开放且未设置 INVITE_CODE：将无人能注册账号")
    if not settings.cookie_secure and settings.backend_host not in ("127.0.0.1", "localhost"):
        # 会话 Cookie 没有 Secure 位却绑在公网地址上 = 明文可被同网段嗅探
        logger.warning("COOKIE_SECURE=false 且后端绑定在 %s：公网部署务必置 true",
                       settings.backend_host)
    if "*" in settings.trusted_proxies:
        # 信所有人转来的 X-Forwarded-For，等于让攻击者自选"我是哪个 IP"，
        # 登录限速与审计日志的 ip 字段会同时失效
        logger.warning("TRUSTED_PROXIES 含通配 *：X-Forwarded-For 可被伪造，"
                       "请只填真实反代地址，并把 uvicorn 的 --forwarded-allow-ips 对齐")
    # 会话按"被使用"滑动续期，过期项在读取时即删；这里清一次存量
    app.state.db.purge_sessions()
    # 上一次进程的未完成任务永远不会自己结束（跑它们的进程已经没了），启动时统一收敛
    abandoned = app.state.db.abandon_unfinished()
    if abandoned:
        logger.warning("已把 %d 条上次进程遗留的任务标记为中断（服务重启导致）", abandoned)
    cleaner = start_cleanup_task(app)
    yield
    cleaner.cancel()
    # 释放 LlmClient 复用的连接池，含配置变更后退役的旧实例
    for client in [*app.state.retired_llms, app.state.llm]:
        await client.aclose()


def _host_of(url_or_origin: str) -> str:
    parts = urlsplit(url_or_origin)
    return (parts.netloc or url_or_origin).lower()


def same_site_ok(origin_header: str, host_header: str, allowed: frozenset[str] = frozenset()) -> bool:
    """写操作的来源必须可信。

    会话 Cookie 已是 SameSite=Lax，跨站 POST 浏览器本来就不会带上；这一层补的是
    Lax 不管的部分：直接打后端端口、绕过前端同源代理的请求。

    允许的来源 = 本机 Host ∪ CORS_ORIGINS。必须带上后者：
    本地开发时 Next 的 rewrite 代理会把 Host 改写成后端端口，
    只比 Host 会让 dev 下所有 POST 全部 403。
    没有 Origin/Referer 的调用（curl、服务端回调）放行 —— 它们本来就拿不到 Cookie。
    """
    if not origin_header:
        return True
    host = _host_of(origin_header)
    return host == host_header.lower() or host in allowed


def trusted_origin_hosts(settings) -> frozenset[str]:
    """CORS_ORIGINS 里声明的来源主机名，同样算可信来源。"""
    return frozenset(_host_of(o) for o in settings.cors_origin_list if o)


def create_app() -> FastAPI:
    app = FastAPI(
        title="跨境智上Agent API",
        version="0.3.0",
        description="CrossList AI · 一键多平台商品上架素材 Agent",
        lifespan=lifespan,
    )

    settings = get_settings()
    allowed = trusted_origin_hosts(settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.retired_llms = []
    app.state.db = Db(settings)
    app.state.allowed_origins = allowed
    # 打包下载的并发额度。放在 create_app 而非 init_runtime：
    # 配置热重载会重跑 init_runtime，而 asyncio.Semaphore 无法安全地中途改容量。
    app.state.download_sem = asyncio.Semaphore(max(1, settings.download_max_concurrency))
    init_dummy_hash()

    @app.middleware("http")
    async def same_site_only(request: Request, call_next):
        """写操作必须来源可信，见 same_site_ok。"""
        if request.method.upper() not in _SAFE_METHODS:
            origin = request.headers.get("origin") or request.headers.get("referer") or ""
            if not same_site_ok(origin, request.headers.get("host", ""),
                                request.app.state.allowed_origins):
                return JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)
        return await call_next(request)

    def init_runtime() -> None:
        """初始化运行期对象；配置变更后只替换模型客户端与存储实现。

        JobManager 持着内存态任务表，整体重建会让所有排队中与运行中的任务瞬间
        变 404、产物页失联；所以这里复用它，仅换掉 llm / storage 引用。
        Db 是外部单例，更不参与重建。
        """
        st = get_settings()
        old_llm = getattr(app.state, "llm", None)
        llm = LlmClient(st)
        storage = Storage(st)
        app.state.settings = st
        app.state.allowed_origins = trusted_origin_hosts(st)   # CORS_ORIGINS 改了就跟着改
        app.state.llm = llm
        app.state.storage = storage
        jobs = getattr(app.state, "jobs", None)
        if jobs is None:
            app.state.jobs = JobManager(st, llm, storage, db=app.state.db)
        else:
            jobs.llm = llm
            jobs.storage = storage
            jobs.settings = st
            # 在途任务的状态里还持有旧客户端，此处不能关闭它，退役等停机再收
            if old_llm is not None and old_llm is not llm:
                app.state.retired_llms.append(old_llm)

    app.state.reload = init_runtime
    init_runtime()

    app.include_router(auth_router)
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
    print(f"  ➜  API 地址：    http://{s.backend_host}:{s.backend_port}")
    print(f"  ➜  接口文档：    http://{s.backend_host}:{s.backend_port}/docs")
    print(f"  ➜  模型接口：    {s.llm_base_url}/chat/completions")
    print(f"  ➜  API Key：     {'已配置' if s.llm_api_key else '未配置，请在 .env 填写 LLM_API_KEY'}")
    print(f"  ➜  注册：        {'开放' if s.allow_open_signup else '需邀请码'}")
    print("")
    uvicorn.run(app, host=s.backend_host, port=s.backend_port, log_level="info")
