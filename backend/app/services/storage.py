"""素材存储：本地磁盘 / 阿里云 OSS

对应方案「存储：阿里云OSS，生成素材按平台分文件夹存储」。
OSS_ENABLED=false 时退化为本地 output/<job_id>/<platform>/ 目录。
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import Settings

logger = logging.getLogger(__name__)


class Storage:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.output_path
        self._oss_bucket = None
        if settings.oss_enabled:
            self._init_oss()

    def _init_oss(self) -> None:
        try:
            import oss2  # noqa: PLC0415

            auth = oss2.Auth(
                self.settings.oss_access_key_id,
                self.settings.oss_access_key_secret,
            )
            self._oss_bucket = oss2.Bucket(
                auth, self.settings.oss_endpoint, self.settings.oss_bucket
            )
            logger.info("已启用阿里云 OSS：%s", self.settings.oss_bucket)
        except Exception as e:  # 配置不完整时回退本地
            logger.warning("OSS 初始化失败，回退本地存储：%s", e)
            self._oss_bucket = None

    # ---------------- 路径 ----------------
    def job_dir(self, job_id: str) -> Path:
        p = self.root / job_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def platform_dir(self, job_id: str, platform: str) -> Path:
        p = self.job_dir(job_id) / platform
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ---------------- 写入 ----------------
    def save(self, job_id: str, rel_path: str, data: bytes) -> str:
        """写入素材，返回可访问路径/URL"""
        abs_path = self.job_dir(job_id) / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(data)

        if self._oss_bucket is not None:
            key = f"{job_id}/{rel_path}".replace("\\", "/")
            try:
                self._oss_bucket.put_object(key, data)
                return f"oss://{self.settings.oss_bucket}/{key}"
            except Exception as e:
                logger.warning("OSS 上传失败（%s），仅保留本地文件", e)
        return str(abs_path)

    def exists(self, job_id: str, rel_path: str) -> bool:
        return (self.job_dir(job_id) / rel_path).exists()

    def read(self, job_id: str, rel_path: str) -> bytes:
        return (self.job_dir(job_id) / rel_path).read_bytes()
