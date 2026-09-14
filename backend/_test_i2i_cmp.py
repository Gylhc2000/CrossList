"""对比：同一参考图下，带图生图 vs 纯文生图的输出差异。

产物落盘到 backend/_i2i_out/{ref,with_ref,text_only}.png
"""
from __future__ import annotations

import asyncio
import base64
import io
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
from app.core.config import get_settings  # noqa: E402

OUT = Path("_i2i_out")
OUT.mkdir(exist_ok=True)

INSTR = (
    "Keep the exact same product as in the reference image: the same shape, the same colors, "
    "the same proportions, the same materials and every visible detail. "
    "Compose it as a clean commercial e-commerce main image on a pure white background, "
    "centered, soft studio lighting, high detail, no text, no watermark."
)


async def gen(content: list, tag: str) -> None:
    s = get_settings()
    parts = urlsplit(s.llm_base_url)
    url = f"{parts.scheme}://{parts.netloc}/api/v1/services/aigc/multimodal-generation/generation"
    payload = {
        "model": s.llm_image_model,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": {"size": "1024*1024", "n": 1},
    }
    async with httpx.AsyncClient(timeout=180.0) as c:
        r = await c.post(
            url,
            headers={"Authorization": f"Bearer {s.llm_api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        d = r.json()
        u = d["output"]["choices"][0]["message"]["content"][0]["image"]
        img = await c.get(u)
    (OUT / f"{tag}.png").write_bytes(img.content)
    print(f"{tag}: {len(img.content) // 1024}KB saved")


async def main() -> None:
    src = Path("output/job_e72f4d9794de/images/main_3_feature1.png")
    from PIL import Image

    im = Image.open(src).convert("RGB")
    im.thumbnail((768, 768))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    raw = buf.getvalue()
    (OUT / "ref.png").write_bytes(raw)
    ref = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
    print("ref saved:", im.size, len(raw) // 1024, "KB")

    await gen([{"image": ref}, {"text": INSTR}], "with_ref")
    await gen([{"text": INSTR}], "text_only")


asyncio.run(main())
