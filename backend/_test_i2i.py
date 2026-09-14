"""探针：验证图像接口是否支持「参考图 + 文本」的图生图（image-to-image）。

用法：
  python _test_i2i.py                 # 默认测 qwen-image-2.0
  python _test_i2i.py qwen-image-edit # 指定模型
"""
from __future__ import annotations

import asyncio
import base64
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402

from app.core.config import get_settings  # noqa: E402


def load_ref(path: Path, max_side: int = 1024) -> str:
    """把参考图缩到 max_side 内并转成 data URL，模拟前端上传的实拍图。"""
    try:
        from PIL import Image

        im = Image.open(path).convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        b = buf.getvalue()
        print(f"  参考图已压缩: {im.size} {len(b) // 1024}KB")
        return "data:image/jpeg;base64," + base64.b64encode(b).decode()
    except Exception as e:  # noqa: BLE001
        print(f"  PIL 不可用({e})，直接原图 base64")
        return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


async def try_call(model: str, content: list, label: str) -> None:
    s = get_settings()
    parts = urlsplit(s.llm_base_url)
    url = f"{parts.scheme}://{parts.netloc}/api/v1/services/aigc/multimodal-generation/generation"
    payload = {
        "model": model,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": {"size": "1024*1024", "n": 1},
    }
    print(f"\n=== [{label}] model={model} content_kinds={[list(c.keys())[0] for c in content]}")
    async with httpx.AsyncClient(timeout=180.0) as c:
        r = await c.post(
            url,
            headers={"Authorization": f"Bearer {s.llm_api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    print(f"  HTTP {r.status_code}")
    txt = r.text
    if len(txt) > 900:
        txt = txt[:900] + "…"
    print("  body:", txt)


async def main() -> None:
    m = sys.argv[1] if len(sys.argv) > 1 else "qwen-image-2.0"
    src = Path("output/job_e72f4d9794de/images/main_1_white.png")
    if not src.exists():
        src = next(Path("output").rglob("*.png"))
    print("参考图:", src)
    ref = load_ref(src)

    instr = (
        "Keep the exact same product as in the reference image: same shape, same colors, "
        "same proportions, same materials and every visible detail. "
        "Re-render it as a clean commercial e-commerce main image on a pure white background, "
        "centered, soft studio lighting, high detail, no text, no watermark."
    )

    await try_call(m, [{"image": ref}, {"text": instr}], "image+text")
    await try_call(m, [{"text": instr}], "text-only (对照)")


asyncio.run(main())
