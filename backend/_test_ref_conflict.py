"""探针 B：复现「白色参考图 → 黑色出图」。

背景：文本模型（deepseek-v4-flash）在本网关**不看图**（返回 200 但 color/shape 全是
none/unknown，见 _test_vision.py），所以知识卡片的 visual_features.color 是纯文字猜的
（耳机多半猜"黑色"）。而 images.py 的图生图提示词把 card 的颜色写进了
"SERIES CONSISTENCY: ... identical colors/finish (黑色...)"，
等于用文字去压制参考图 → 白色参考图被画成黑色。

本探针：
  1. 造一张白色耳机参考图；
  2. 用「含卡片颜色」的旧提示词出图 —— 预期偏黑/跑偏；
  3. 用「只锁参考图、不带任何文字颜色」的新提示词出图 —— 预期保持白色。

用法：python _test_ref_conflict.py
"""
from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path

from app.core.config import get_settings
from app.core.llm import LlmClient
from PIL import Image

OUT = Path("_i2i_out")
OUT.mkdir(exist_ok=True)
SIZE = "1024*1024"

WHITE_REF_PROMPT = (
    "professional e-commerce product photograph of a pair of pure white true wireless "
    "in-ear earbuds standing in an open pure white charging case, matte plastic finish, "
    "no logo, no text, soft studio lighting, light grey seamless background, high detail"
)

# 旧（有问题）的图生图提示词：把卡片猜的颜色写进"必须一致"
OLD_BASE = (
    "the EXACT product shown in the attached reference photo — keep its identical shape, "
    "proportions, colors, materials, surface finish and every visible detail; "
    "do not redesign, restyle or substitute it with a different product."
    " SERIES CONSISTENCY: every image shows the exact same product — identical model, "
    "identical design and identical colors/finish (黑色 (Black)、塑料 (Plastic)、入耳式 (In-ear)). "
    "The overall color scheme and every component, part and visible detail must be exactly "
    "the same in all images. Part of one coherent product photoshoot with consistent lighting "
    "and proportions. "
    "high quality commercial product photography, sharp detail, professional studio lighting, 8k"
)

# 新（修复后）的图生图提示词：外观一律以参考图为准，不出现任何文字颜色描述
NEW_BASE = (
    "the EXACT product shown in the attached reference photo — keep its identical shape, "
    "proportions, colors, materials, surface finish and every visible detail; "
    "do not redesign, restyle, recolor or substitute it with a different product. "
    "The overall color scheme and every component, part and visible detail must be exactly "
    "the same as in the reference photo. Part of one coherent product photoshoot with "
    "consistent lighting and proportions. "
    "high quality commercial product photography, sharp detail, professional studio lighting, 8k"
)

TEMPLATE = "{base}, on a pure white background (#FFFFFF), centered composition, no text, no watermark, no props"


async def save_img(llm: LlmClient, prompt: str, tag: str, refs=None) -> None:
    data = await llm.generate_image(prompt, size=SIZE, ref_images=refs)
    (OUT / f"{tag}.png").write_bytes(data)
    print(f"  {tag}: {len(data) // 1024}KB")


async def main() -> None:
    s = get_settings()
    llm = LlmClient(s, timeout=240.0)

    if not (OUT / "white_ref.png").exists():
        print("生成白色参考图…")
        await save_img(llm, WHITE_REF_PROMPT, "white_ref")
    ref_path = OUT / "white_ref.png"
    im = Image.open(ref_path).convert("RGB")
    im.thumbnail((1024, 1024), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    ref = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    print("参考图尺寸:", im.size, f"{len(buf.getvalue()) // 1024}KB")

    print("① 旧提示词（含卡片猜的『黑色』）+ 白色参考图 …")
    await save_img(llm, TEMPLATE.format(base=OLD_BASE), "out_old_prompt", refs=[ref])

    print("② 新提示词（不写颜色，只锁参考图）+ 白色参考图 …")
    await save_img(llm, TEMPLATE.format(base=NEW_BASE), "out_new_prompt", refs=[ref])

    print("产物:", OUT.resolve())


asyncio.run(main())
