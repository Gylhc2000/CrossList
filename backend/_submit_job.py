"""提交一个带实拍参考图的任务（用于复验参考图保真）。

用法：python _submit_job.py <参考图路径> [商品名] [描述]
"""
from __future__ import annotations

import base64
import io
import sys
from pathlib import Path

import httpx
from PIL import Image

ref = Path(sys.argv[1])
name = sys.argv[2] if len(sys.argv) > 2 else "无线蓝牙耳机"
desc = sys.argv[3] if len(sys.argv) > 3 else "真无线入耳式蓝牙耳机，配充电仓，支持触控操作。"

im = Image.open(ref).convert("RGB")
im.thumbnail((1600, 1600), Image.LANCZOS)
buf = io.BytesIO()
im.save(buf, format="JPEG", quality=85)
data_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
print(f"参考图 {ref} -> {im.size} {len(buf.getvalue()) // 1024}KB")

payload = {
    "productName": name,
    "category": "3C数码 / 耳机",
    # 刻意不提颜色：卡片的外观只能来自视觉理解
    "description": desc,
    "specs": "充电仓容量：400mAh\n单次续航：6小时\n蓝牙：5.3",
    "brand": "",
    "price": 29.99,
    "currency": "USD",
    "platforms": ["amazon"],
    "markets": ["us"],
    "platformMarkets": {"amazon": ["us"]},
    "images": [data_url],
    "withImages": True,
}

r = httpx.post("http://localhost:8000/api/jobs", json=payload, timeout=60)
print(r.status_code, r.text)
Path("_last_job.txt").write_text(r.json()["jobId"], encoding="utf-8")
