"""图片平台适配：按平台 image_px 缩放 + 白底补边成正方形

策略：
  - 等比缩放至目标像素内（ImageOps.contain，只缩不放大小图避免糊）
  - 居中贴到纯白画布补成正方形（满足 1:1 主图要求，白底即 Amazon 主图底色）
  - 无法解析的文件（如降级占位 SVG）返回 None，调用方回落原样复制

纯 CPU 本地操作，调用方应通过 asyncio.to_thread 执行以免阻塞事件循环。
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps

MAX_UPSCALE = 2  # 源图小于目标时最多放大倍数，避免暴力拉伸变糊


def fit_image_bytes(data: bytes, target_px: int) -> bytes | None:
    """将图片适配为 target_px × target_px 的 PNG，失败返回 None。"""
    if target_px <= 0:
        return None
    try:
        img = Image.open(BytesIO(data))
        img.load()
    except Exception:
        return None

    try:
        # 透明通道合成到白底（PNG 带 alpha 时主图会出现黑底问题）
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # 等比缩放到目标框内；小图最多放大 MAX_UPSCALE 倍
        scale = target_px / max(img.size)
        if scale > MAX_UPSCALE:
            scale = MAX_UPSCALE
        box = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
        img = ImageOps.contain(img, box, method=Image.LANCZOS)

        # 白底正方形画布，居中贴图
        canvas = Image.new("RGB", (target_px, target_px), (255, 255, 255))
        canvas.paste(img, ((target_px - img.width) // 2, (target_px - img.height) // 2))

        buf = BytesIO()
        # 不开 optimize：实测 2000px 单张 4.5s -> 0.56s，体积仅增 ~10%
        canvas.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def to_jpeg_bytes(data: bytes, quality: int = 90) -> bytes | None:
    """任意图片 → JPEG。

    Amazon 的免 URL 图片包（zip 按 `<SKU>.MAIN.jpg` 命名上传）走的是 .jpg 扩展名，
    而我们内部统一产出 PNG，所以出包时转一次。顺带把体积压下来：2000px 白底 PNG
    单张 3~6MB，同样画面 JPEG 约 0.5~1MB —— 9 张图的包从「上百 MB」变成能下载。

    纯 CPU，调用方应放进线程池。失败返回 None，由调用方决定跳过还是回落。
    """
    try:
        img = Image.open(BytesIO(data))
        img.load()
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()
    except Exception:
        return None
