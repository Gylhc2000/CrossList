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
    # 视觉理解模型（parse 节点携带实拍图做多模态解析时使用）。
    # 必须选**真正支持看图**的模型：实测 deepseek-v4-flash 在本网关会返回 200
    # 但内容里 color/shape 全是 unknown/none —— 图片被静默忽略、且不抛错，
    # 于是知识卡片的颜色等外观字段退化成"猜"（耳机基本猜黑色），
    # 再被写进图像提示词，反过来把白色实拍图压成黑色。
    llm_vision_model: str = "qwen3.6-flash"
    llm_image_model: str = "qwen-image-2.0"
    llm_audio_model: str = "qwen-audio-3.0-tts-plus"
    # 关闭文本模型的思维链（请求里带 enable_thinking=false）。
    # 实测 deepseek-v4-flash 在本网关**默认开启 thinking**：思维链会吃光整个 max_tokens，
    # content 直接返回空串 —— 表现为「首次生成失败，正在重试：模型未返回合法 JSON：（空）」，
    # 两个站点因此丢掉 Listing；且单次耗时 40~80s。关掉后同一 prompt 稳定返回完整 JSON，
    # 单次只要 7s（实测 7/7 成功，两个模型都接受该参数）。
    # 若换成不支持该参数的模型，llm.py 会在 400 时自动去掉该参数重试一次。
    llm_disable_thinking: bool = True
    # 图像接口独立基地址：留空则复用 llm_base_url。
    # 若网关未代理 /images/generations，可在这里单独指向可用的图像服务地址。
    llm_image_base_url: str = ""

    # 服务
    backend_port: int = 8000
    output_dir: str = "./output"
    cors_origins: str = "http://localhost:3000"
    # 同时运行的 Agent 任务数上限：每个任务内部还有 listing/图像并发，
    # 多任务叠加易触发模型限流；超出上限的任务排队等待（状态保持 queued）
    job_max_concurrency: int = 2

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
