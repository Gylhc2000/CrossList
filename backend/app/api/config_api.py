"""配置接口：读写 .env、测试模型连通性

默认整体关闭（ALLOW_RUNTIME_CONFIG=false）：这两个接口能改写模型服务地址，
未鉴权时可被用来把真实 API Key 外送到攻击者的服务器，等于密钥外泄 + SSRF。
确有需要时打开，且仅管理员可用。
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.audit import audit
from app.core.auth import User, require_admin, require_user
from app.core.config import BASE_DIR, get_settings
from app.core.llm import LlmClient
from app.core.redact import redact

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["config"])

ENV_FILE = BASE_DIR / ".env"

# 允许前端修改的键：键名必须与 ConfigPatch 的字段名逐字一致，
# 否则下面的 getattr(patch, field) 取不到值，配置会被静默丢弃（接口仍返回 ok）
WRITABLE = {
    "baseUrl": "LLM_BASE_URL",
    "apiKey": "LLM_API_KEY",
    "textModel": "LLM_TEXT_MODEL",
    "imageModel": "LLM_IMAGE_MODEL",
    "audioModel": "LLM_AUDIO_MODEL",
}


class ConfigPatch(BaseModel):
    baseUrl: str | None = None
    apiKey: str | None = None
    textModel: str | None = None
    imageModel: str | None = None
    audioModel: str | None = None


def _write_env(patch: dict[str, str]) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    out: list[str] = []
    hit = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in patch:
                out.append(f"{k}={patch[k]}")
                hit.add(k)
                continue
        out.append(line)
    for k, v in patch.items():
        if k not in hit:
            out.append(f"{k}={v}")
    # 先写同目录临时文件再 os.replace：直接 write_text 截断重写时，进程崩溃或并发读
    # 会拿到半截文件（Key 丢了就是后续任务全败）。同目录保证 replace 是原子的。
    tmp = ENV_FILE.parent / (ENV_FILE.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        # .env 装着密钥，权限收紧到仅属主可读写（新建时默认 umask 是 0644）
        os.chmod(tmp, 0o600)
        os.replace(tmp, ENV_FILE)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _mask(key: str) -> str:
    """API Key 脱敏：仅展示前 4 位 + 尾 4 位，明文永不出后端。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


def _runtime_config_enabled(request: Request) -> None:
    """改写模型地址/Key 的能力默认关掉，打开也只给管理员。"""
    if not getattr(request.app.state.settings, "allow_runtime_config", False):
        raise HTTPException(403, "运行时模型配置已禁用（需在服务端 .env 设 ALLOW_RUNTIME_CONFIG=true）")


@router.get("/config")
async def get_config(request: Request, user: User = Depends(require_user)):
    s = get_settings()
    return {
        "baseUrl": s.llm_base_url,
        "apiKey": _mask(s.llm_api_key),   # 脱敏展示，仅用于确认配置状态
        "textModel": s.llm_text_model,
        "imageModel": s.llm_image_model,
        "audioModel": s.llm_audio_model,
        "hasKey": bool(s.llm_api_key),
        # 前端据此决定"模型配置"入口是否可写，避免只读状态下给人一个能点的保存钮
        "runtimeConfig": bool(s.allow_runtime_config),
    }


@router.put("/config")
async def update_config(patch: ConfigPatch, request: Request,
                        user: User = Depends(require_admin)):
    _runtime_config_enabled(request)
    env_patch: dict[str, str] = {}
    for field, env_key in WRITABLE.items():
        val = getattr(patch, field, None)
        if val is None:
            continue
        val = str(val).strip()
        # 空字符串 = 未修改（前端不回显旧值），避免把空值写回覆盖真实 Key。
        # 同理，带掩码星号的值是 GET /config 回显的 sk-x****abcd，
        # 原样写盘就等于用掩码覆盖了真 Key
        if field == "apiKey" and (val == "" or "****" in val):
            continue
        env_patch[env_key] = val
    if env_patch:
        # 只记键名不记值 —— 值里就是 API Key
        audit("config_write", request=request, user=user, keys=",".join(sorted(env_patch)))
        _write_env(env_patch)
        get_settings.cache_clear()
        # 重建依赖配置的运行期对象
        request.app.state.reload()
    s = get_settings()
    return {"ok": True, "hasKey": bool(s.llm_api_key)}


class TestBody(BaseModel):
    baseUrl: str | None = None
    apiKey: str | None = None
    textModel: str | None = None


@router.post("/test")
async def test_connection(body: TestBody, request: Request,
                          user: User = Depends(require_admin)):
    """连通性测试。会按请求里的 baseUrl 发真实请求，因此与 PUT /config 同级管控：
    未鉴权时它是一个带回显的 SSRF，且 apiKey 缺省会带上服务端真实 Key。
    """
    from app.core.config import Settings  # 局部导入避免循环

    _runtime_config_enabled(request)
    cur = get_settings()
    tmp = Settings(
        llm_base_url=body.baseUrl or cur.llm_base_url,
        llm_api_key=body.apiKey if body.apiKey is not None else cur.llm_api_key,
        llm_text_model=body.textModel or cur.llm_text_model,
    )
    tmp_client = LlmClient(tmp)
    try:
        result = await tmp_client.ping()
        # 字段名在接口边界统一成 camelCase（与 /api/config、/api/meta 一致）。
        # 直接 **result 会把 ping 的 snake_case 带出去，前端读 r.latencyMs 得 undefined。
        return {"ok": True, "model": result["model"], "latencyMs": result["latency_ms"]}
    except Exception as e:
        # 上游报文常带网关主机名与响应片段，对外只给脱敏后的短句（原文进日志）
        logger.warning("模型连通性测试失败：%s", str(e)[:500])
        return {"ok": False, "error": redact(e)}
    finally:
        await tmp_client.aclose()
