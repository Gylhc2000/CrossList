"""配置接口：读写 .env、测试模型连通性"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.config import BASE_DIR, get_settings
from app.core.llm import LlmClient, LlmError

router = APIRouter(prefix="/api", tags=["config"])

ENV_FILE = BASE_DIR / ".env"

# 允许前端修改的键
WRITABLE = {
    "base_url": "LLM_BASE_URL",
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


def _read_env() -> dict[str, str]:
    data: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()
    return data


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
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


def _mask(key: str) -> str:
    """API Key 脱敏：仅展示前 4 位 + 尾 4 位，明文永不出后端。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


@router.get("/config")
async def get_config():
    s = get_settings()
    return {
        "baseUrl": s.llm_base_url,
        "apiKey": _mask(s.llm_api_key),   # 脱敏展示，仅用于确认配置状态
        "textModel": s.llm_text_model,
        "imageModel": s.llm_image_model,
        "audioModel": s.llm_audio_model,
        "hasKey": bool(s.llm_api_key),
    }


@router.put("/config")
async def update_config(patch: ConfigPatch, request: Request):
    env_patch: dict[str, str] = {}
    for field, env_key in WRITABLE.items():
        val = getattr(patch, field, None)
        if val is None:
            continue
        val = str(val).strip()
        # 空字符串 = 未修改（前端不再回传旧值），避免把掩码/空值写回覆盖真实 Key
        if field == "apiKey" and val == "":
            continue
        env_patch[env_key] = val
    if env_patch:
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
async def test_connection(body: TestBody):
    from app.core.config import Settings  # 局部导入避免循环

    cur = get_settings()
    tmp = Settings(
        llm_base_url=body.baseUrl or cur.llm_base_url,
        llm_api_key=body.apiKey if body.apiKey is not None else cur.llm_api_key,
        llm_text_model=body.textModel or cur.llm_text_model,
    )
    try:
        result = await LlmClient(tmp).ping()
        return {"ok": True, **result}
    except LlmError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
