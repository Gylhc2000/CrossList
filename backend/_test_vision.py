"""探针 A：文本模型是否真的具备视觉理解能力。

关键问题：parse 节点带着 image_url 调 chat/completions，如果网关悄悄忽略图片、
不报错，知识卡片就会基于纯文字生成 —— 颜色等外观字段全靠猜（耳机默认猜黑色），
随后被写进图像提示词，反过来压制参考图。

用法：python _test_vision.py [图片路径] [模型名]
"""
from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from app.core.config import get_settings  # noqa: E402

P = Path(sys.argv[1] if len(sys.argv) > 1 else "output/job_ab6f5827f1c9/images/main_1_white.png")
MODELS = sys.argv[2].split(",") if len(sys.argv) > 2 else None


async def ask(model: str, data_url: str, timeout: float = 180.0) -> None:
    s = get_settings()
    q = (
        "这张图里是什么商品？请只回答 JSON："
        '{"color":"商品主色","brand_text":"图上能看到的品牌文字，没有就填 none",'
        '"pattern":"商品上的图案或标记，没有就填 none","shape":"外形描述"}'
    )
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": q},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "temperature": 0.2,
        "max_tokens": 800,
    }
    print(f"\n=== model={model} ===")
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(
                f"{s.llm_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {s.llm_api_key}"},
                json=body,
            )
        print("HTTP", r.status_code)
        d = r.json()
        if d.get("code"):
            print("code:", d.get("code"), str(d.get("message"))[:200])
        ch = (d.get("choices") or [{}])[0]
        print("finish:", ch.get("finish_reason"))
        print("content:", ((ch.get("message") or {}).get("content") or "")[:600])
    except Exception as e:  # noqa: BLE001
        print("FAILED:", type(e).__name__, str(e)[:200])


async def main() -> None:
    s = get_settings()
    b64 = base64.b64encode(P.read_bytes()).decode()
    data_url = "data:image/png;base64," + b64
    print("图片:", P, f"{P.stat().st_size // 1024}KB")
    for m in MODELS or [s.llm_text_model]:
        await ask(m, data_url)


asyncio.run(main())
