"""OpenAI 兼容协议客户端：chat/completions + images/generations

- 鉴权：Authorization: Bearer <APIKey>
- 对话：{base}/chat/completions
- 图像：{base}/images/generations
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
from typing import Any

import httpx

from app.core.config import Settings


class LlmError(RuntimeError):
    pass


# 图像接口的内容风控错误码：命中说明「提示词/出图结果被判定为疑似侵权或敏感」，
# 换更通用的提示词重试往往可通过；与限流/鉴权等错误区分对待。
CONTENT_RISK_CODES = (
    "IPInfringementSuspect",
    "DataInspectionFailed",
    "SensitiveContentDetected",
    "ComplianceViolation",
)


def is_content_risk(err: Exception | str) -> bool:
    """判断图像生成失败是否属于内容风控（IP 侵权/敏感内容）而非接口故障。"""
    msg = err if isinstance(err, str) else str(err)
    return any(code in msg for code in CONTENT_RISK_CODES)


def _normalize_base(url: str) -> str:
    return url.rstrip("/")


class LlmClient:
    def __init__(self, settings: Settings, timeout: float = 120.0):
        self.settings = settings
        self.base = _normalize_base(settings.llm_base_url)
        # 图像接口可独立配置（网关未代理 /images/generations 时指向其他地址）
        self.image_base = _normalize_base(settings.llm_image_base_url or settings.llm_base_url)
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        if not self.settings.llm_api_key:
            raise LlmError("尚未配置 API Key（LLM_API_KEY）")
        return {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4000,
        json_mode: bool = False,
        retries: int = 2,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model or self.settings.llm_text_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_err: Exception | None = None
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(retries + 1):
                try:
                    resp = await client.post(
                        f"{self.base}/chat/completions",
                        headers=self._headers(),
                        json=payload,
                    )
                    if resp.status_code >= 400:
                        # 某些模型不支持 response_format，去掉后重试
                        if json_mode and resp.status_code == 400:
                            payload.pop("response_format", None)
                            continue
                        raise LlmError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                    data = resp.json()
                    choices = data.get("choices") or []
                    if not choices:
                        raise LlmError(f"接口未返回 choices：{str(data)[:200]}")
                    content = (choices[0].get("message") or {}).get("content") or ""
                    # 部分模型（如 deepseek-v4-flash）带 response_format 时对长 prompt
                    # 返回 200 但 content 为空；去掉 json_object 重试一次。
                    # 提示词本身已要求输出 JSON，降级不影响格式解析。
                    if json_mode and not content.strip() and "response_format" in payload:
                        payload.pop("response_format", None)
                        continue
                    # 网关偶发返回 200 空内容（与 json_mode 无关），按可重试错误处理；
                    # 重试耗尽仍为空时必须抛错——返回空串会让下游拿到
                    # 「模型未返回合法 JSON：（空）」，把好好的 Listing 打成 0 分。
                    if not content.strip():
                        if attempt < retries:
                            last_err = LlmError("200 但 content 为空")
                            await asyncio.sleep(1.0)
                            continue
                        raise LlmError("200 但 content 为空（重试耗尽）")
                    return content
                except LlmError:
                    raise
                except Exception as e:  # 网络类错误重试
                    last_err = e
                    if attempt < retries:
                        await asyncio.sleep(2.0)
        # 把异常类型带上，便于定位是超时/连接拒绝还是其他
        detail = f"{type(last_err).__name__}: {last_err}" if last_err else "未知错误"
        raise LlmError(f"调用对话接口失败：{detail}")

    async def chat_json(self, *args, **kwargs) -> dict[str, Any]:
        raw = await self.chat(*args, json_mode=True, **kwargs)
        return extract_json(raw)

    async def generate_image(
        self,
        prompt: str,
        model: str | None = None,
        size: str = "1024x1024",
        seed: int | None = None,
        ref_images: list[str] | None = None,
    ) -> bytes:
        """生成图像，返回图片二进制。

        默认走 DashScope 原生接口 /api/v1/services/aigc/multimodal-generation/generation
        （qwen-image / wan 系列实测可用；该系列模型不提供 OpenAI compatible-mode）。
        仅当显式配置 LLM_IMAGE_BASE_URL 时，才走 OpenAI 兼容 /images/generations。
        seed：同一任务的多张图传相同 seed 可提升系列一致性；
        若网关不支持 seed 参数，自动降级为不带 seed 重试一次。
        ref_images：参考图（data URL 或 http URL）列表，传入即为「图生图」——
        模型会以参考图中的实物为基准出图，保证商品外观与用户实拍图一致。
        """
        if self.settings.llm_image_base_url:
            return await self._image_via_openai(prompt, model, size)
        try:
            return await self._image_via_dashscope(prompt, model, size, seed=seed, ref_images=ref_images)
        except LlmError:
            if seed is None:
                raise
            return await self._image_via_dashscope(prompt, model, size, seed=None, ref_images=ref_images)

    async def _image_via_dashscope(
        self,
        prompt: str,
        model: str | None,
        size: str,
        seed: int | None = None,
        ref_images: list[str] | None = None,
    ) -> bytes:
        from urllib.parse import urlsplit

        parts = urlsplit(self.base)  # 从对话基地址提取 scheme://host（即 {WorkspaceId} 域名）
        url = f"{parts.scheme}://{parts.netloc}/api/v1/services/aigc/multimodal-generation/generation"
        params: dict = {
            "size": str(size).replace("x", "*"),  # DashScope 用 1024*1024
            "n": 1,
        }
        if seed is not None:
            params["seed"] = seed
        # 参考图在前、文本指令在后：DashScope 多模态生成按此顺序理解「以该图为基准」
        content: list[dict] = [{"image": u} for u in (ref_images or []) if u]
        content.append({"text": prompt})
        payload = {
            "model": model or self.settings.llm_image_model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": params,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            if resp.status_code >= 400:
                raise LlmError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            d = resp.json()
            if d.get("code"):
                raise LlmError(f"{d['code']}: {str(d.get('message', ''))[:200]}")
            try:
                img_url = d["output"]["choices"][0]["message"]["content"][0]["image"]
            except (KeyError, IndexError, TypeError):
                raise LlmError(f"图像接口返回结构异常：{str(d)[:200]}")
            if not img_url:
                raise LlmError("图像接口未返回 image URL")
            img = await client.get(img_url)
            if img.status_code >= 400:
                raise LlmError(f"下载生成图像失败 HTTP {img.status_code}")
            return img.content

    async def _image_via_openai(
        self, prompt: str, model: str | None, size: str
    ) -> bytes:
        """返回图片二进制。优先 b64_json，其次 url 下载。"""
        payload = {
            "model": model or self.settings.llm_image_model,
            "prompt": prompt,
            "size": size,
            "n": 1,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.image_base}/images/generations",
                headers=self._headers(),
                json=payload,
            )
            if resp.status_code >= 400:
                raise LlmError(
                    f"HTTP {resp.status_code}: {resp.text[:200]}"
                    f"（图像接口 {self.image_base}/images/generations）"
                )
            data = resp.json()
            items = data.get("data") or []
            if not items:
                raise LlmError(f"图像接口未返回数据：{str(data)[:200]}")
            item = items[0]
            if item.get("b64_json"):
                return base64.b64decode(item["b64_json"])
            if item.get("url"):
                img = await client.get(item["url"])
                return img.content
            raise LlmError("图像接口既无 b64_json 也无 url")

    async def ping(self) -> dict[str, Any]:
        """连通性测试：发一条极短消息"""
        import time

        t0 = time.perf_counter()
        await self.chat(
            [{"role": "user", "content": "reply with: ok"}],
            temperature=0,
            max_tokens=16,
        )
        return {
            "ok": True,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "model": self.settings.llm_text_model,
        }


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)
# BOM / 零宽字符：strip() 不认它们是空白，会残留并让 json.loads 报
# 「Unexpected UTF-8 BOM」等错，且肉眼看起来像「报错后面是空的」
_INVISIBLE = "\ufeff\u200b\u200c\u200d\u2060"


def _sanitize_json_text(raw: str) -> str:
    """清洗模型输出：去思维链、去不可见字符。"""
    t = _THINK_BLOCK.sub("", raw)
    t = "".join(ch for ch in t if ch not in _INVISIBLE)
    return t.strip()


def _escape_control_chars(text: str) -> str:
    """把 JSON 字符串**内部**的裸控制字符转义为合法形式。

    deepseek 偶发在 comments/claim 里直接换行（未转义 \n），
    json.loads 会报 Invalid control character，且截断修复救不了。
    """
    out: list[str] = []
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
                out.append(ch)
                continue
            if ch == "\\":
                esc = True
                out.append(ch)
                continue
            if ch == '"':
                in_str = False
                out.append(ch)
                continue
            if ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            else:
                out.append(ch)
            continue
        if ch == '"':
            in_str = True
        out.append(ch)
    return "".join(out)


def _repair_truncated_json(raw: str) -> dict[str, Any] | None:
    """尽力修复被 max_tokens 截断的 JSON（评分类输出常见）。

    做法：去掉围栏与前后垃圾 → 补悬挂引号 → 按栈补齐未闭合的 {} []。
    只求救回简单结构（score/comments/suggestions 这类），失败返回 None。
    """
    if not raw or "{" not in raw:
        return None
    t = raw.strip()
    m = _JSON_FENCE.search(t)
    if m:
        t = m.group(1).strip()
    if "{" not in t:
        return None
    t = t[t.index("{"):]
    if t.count('"') % 2 == 1:          # 截断在字符串中间：补悬挂引号
        t += '"'
    t = t.rstrip().rstrip(",")
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in t:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack and (stack[-1] == "{" and ch == "}" or stack[-1] == "[" and ch == "]"):
                stack.pop()
    for ch in reversed(stack):
        t += "}" if ch == "{" else "]"
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def extract_json(raw: str) -> dict[str, Any]:
    """从模型输出中稳健地提取 JSON 对象"""
    if raw is None:
        raise LlmError("模型返回为空")
    text = _sanitize_json_text(raw)
    # 逐级尝试：原文 → 转义字符串内裸控制字符后（deepseek 偶发未转义换行）
    for candidate in (text, _escape_control_chars(text)):
        candidate = candidate.strip()
        for body in (candidate, *_JSON_FENCE.findall(candidate)):
            body = body.strip()
            try:
                obj = json.loads(body)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                continue
    # 退化：截取首个 { 到最后一个 }（顺带处理裸控制字符变体）
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        for seg in (text[start : end + 1], _escape_control_chars(text[start : end + 1])):
            try:
                obj = json.loads(seg)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                continue
    # 最后的兜底：截断 JSON 补括号修复（deepseek 评分输出较长时偶发）
    repaired = _repair_truncated_json(_escape_control_chars(text))
    if repaired is not None:
        return repaired
    raise LlmError(f"模型未返回合法 JSON：{text[:200] or '（空）'}")
