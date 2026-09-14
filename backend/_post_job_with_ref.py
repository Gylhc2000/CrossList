"""验证：带实拍参考图提交任务，确认素材图与参考图一致。

用法：python _post_job_with_ref.py <参考图路径>
"""
from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx
from PIL import Image

REF = Path(sys.argv[1] if len(sys.argv) > 1 else "_i2i_out/ref.png")

im = Image.open(REF).convert("RGB")
im.thumbnail((1600, 1600), Image.LANCZOS)
buf = io.BytesIO()
im.save(buf, format="JPEG", quality=85)
data_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
print(f"参考图 {REF} -> {im.size} {len(buf.getvalue()) // 1024}KB")

payload = {
    "productName": "无线蓝牙降噪耳机",
    "category": "3C数码 / 耳机",
    "description": "真无线入耳式蓝牙耳机，配备充电仓，支持主动降噪与触控操作。",
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
job = r.json()["jobId"]
Path("_last_ref_job.txt").write_text(job, encoding="utf-8")
print("JOB", job, "->", f"http://localhost:3000/?job={quote(job)}")
