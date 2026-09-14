"""拼一张「参考图 / 修复前 / 修复后」对照图，用于验收展示。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
S = 420
GAP = 16
PAD = 20
LABEL_H = 44

items = [
    ("参考图（用户实拍）", Path("_i2i_out/ref.png")),
    ("修复前 · 纯文生图", Path("_i2i_out/text_only.png")),
    ("修复后 · 以参考图生成", Path("output/job_3dee3381ca3c/images/main_1_white.png")),
]

font = None
for fp in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf"):
    if Path(fp).exists():
        font = ImageFont.truetype(fp, 20)
        break
if font is None:
    font = ImageFont.load_default()

n = len(items)
W = PAD * 2 + S * n + GAP * (n - 1)
H = PAD * 2 + LABEL_H + S
canvas = Image.new("RGB", (W, H), (247, 248, 250))
d = ImageDraw.Draw(canvas)

for i, (label, rel) in enumerate(items):
    x = PAD + i * (S + GAP)
    d.text((x, PAD + 8), label, fill=(29, 33, 41), font=font)
    y = PAD + LABEL_H
    p = ROOT / rel
    im = Image.open(p).convert("RGB")
    im.thumbnail((S, S), Image.LANCZOS)
    box = Image.new("RGB", (S, S), (255, 255, 255))
    box.paste(im, ((S - im.width) // 2, (S - im.height) // 2))
    canvas.paste(box, (x, y))
    d.rectangle([x, y, x + S - 1, y + S - 1], outline=(226, 230, 238))

out = ROOT / "_fix_reference_compare.png"
canvas.save(out)
print("saved", out, canvas.size)
