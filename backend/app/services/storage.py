"""素材存储：本地磁盘为唯一真相，OSS 只是可选镜像

对应方案「存储：生成素材按平台分文件夹存储」。
`OSS_ENABLED=false`（默认）时只写本地 `output/<job_id>/<platform>/`；开着时额外
把每个对象复制一份到 OSS 作为备份。

这里刻意不做的事：把 OSS 当成"图片公网地址"的来源。卖家不该为了用这个工具先去
配置图床 —— 交付物是包内的图片文件本身（Amazon 有按 SKU 命名的 zip 直传通道，
其余平台拖进后台媒体库即可），所以 `_save` 不返回任何 URL，下载一律走本服务的 zip。
"""
from __future__ import annotations

import asyncio
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

    def job_path(self, job_id: str) -> Path:
        """只算路径、**不建目录**。

        job_dir() 顺手 mkdir 看着无害，但读路径（列清单、打包、取素材）也会调用它：
        刚被删掉的任务只要被查一次，目录就带着空壳复活，产物"删不掉"的假象由此而来。
        写入用 job_dir()，读取一律用本方法。
        """
        return self.root / job_id

    def resolve_in_job(self, job_id: str, rel: str) -> Path | None:
        """把调用方给的相对路径解析到任务目录内，越界返回 None。

        素材文件名、打包 scope 都来自查询参数，必须当作不可信输入：绝对路径会让
        (root / rel) 整体替换掉 root，../ 则逐级上跳，两者都能读出 .env 和其它任务。
        先 resolve() 再判 is_relative_to，顺带挡住指向目录外的符号链接。
        """
        if not rel:
            return None
        root = self.job_path(job_id).resolve()
        candidate = (root / rel).resolve()
        return candidate if candidate.is_relative_to(root) else None

    # ---------------- 写入 ----------------
    async def save(self, job_id: str, rel_path: str, data: bytes) -> None:
        """落盘一个产物；OSS 开着时顺带镜像一份。

        不返回 URL：`oss://bucket/key` 不是可访问地址，而真正会读它的调用方一个也没有
        —— 卖家拿到的始终是 zip。留着一个没人用的伪 URL 返回值，只会让人误以为
        产物有公网入口、可以直接填进平台模板的图片列。

        落盘和上传都是同步 IO：留在事件循环里时，每传一张图整个进程的
        SSE 与进度推送都会停摆，所以一律放进线程池执行。
        """
        await asyncio.to_thread(self._save, job_id, rel_path, data)

    def _save(self, job_id: str, rel_path: str, data: bytes) -> None:
        abs_path = self.job_dir(job_id) / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(data)

        if self._oss_bucket is not None:
            key = f"{job_id}/{rel_path}".replace("\\", "/")
            try:
                self._oss_bucket.put_object(key, data)
            except Exception as e:
                logger.warning("OSS 镜像上传失败（%s），本地文件仍完整可用", e)

    async def exists(self, job_id: str, rel_path: str) -> bool:
        return await asyncio.to_thread(lambda: (self.job_path(job_id) / rel_path).exists())

    async def read(self, job_id: str, rel_path: str) -> bytes:
        return await asyncio.to_thread(lambda: (self.job_path(job_id) / rel_path).read_bytes())
