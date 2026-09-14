"""全局配置（pydantic-settings，读取 .env）"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]  # backend/


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 大模型
    llm_base_url: str = "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = ""
    llm_text_model: str = "qwen3.8-max"
    # 评分等关键环节的兜底模型：主模型连续失败时降级调用一次，避免整环节 0 分
    llm_text_model_fallback: str = "qwen3.6-flash"
    llm_image_model: str = "qwen-image-2.0"
    llm_audio_model: str = "qwen-audio-3.0-tts-plus"
    # 图像接口独立基地址：留空则复用 llm_base_url。
    # 若网关未代理 /images/generations，可在这里单独指向可用的图像服务地址。
    llm_image_base_url: str = ""

    # 服务
    backend_port: int = 8000
    output_dir: str = "./output"
    cors_origins: str = "http://localhost:3000"

    # 产物清理：任务结束（含重启遗留的孤儿目录）超过 TTL 后删除，
    # 每 cleanup_interval_minutes 分钟执行一轮；设为 0 可禁用
    cleanup_ttl_hours: float = 24.0
    cleanup_interval_minutes: float = 30.0

    # 阿里云 OSS
    oss_enabled: bool = False
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    oss_bucket: str = ""
    oss_endpoint: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def output_path(self) -> Path:
        p = Path(self.output_dir)
        if not p.is_absolute():
            p = BASE_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


# 可选模型清单（与方案 4.3 对齐）
TEXT_MODELS = [
    "qwen3.8-max", "qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus", "qwen3.6-flash",
    "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v3.2",
    "kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5",
    "glm-5.2", "glm-5.1", "glm-5", "MiniMax-M2.5",
]
IMAGE_MODELS = ["qwen-image-2.0", "qwen-image-2.0-pro", "wan2.7-image", "wan2.7-image-pro"]
AUDIO_MODELS = ["qwen-audio-3.0-tts-plus", "qwen-audio-3.0-realtime-plus"]
