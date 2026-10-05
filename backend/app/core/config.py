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
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
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
    # 默认只监听回环：公网机器上 0.0.0.0 等于把接口直接暴露给扫描器。
    # 需要外部访问时显式设 BACKEND_HOST=0.0.0.0，并务必同时打开 COOKIE_SECURE。
    backend_host: str = "127.0.0.1"
    output_dir: str = "./output"
    cors_origins: str = "http://localhost:3000"

    # ---- 账号与会话（SQLite，标准库实现，无额外依赖）----
    data_dir: str = "./data/crosslist.db"
    session_ttl_days: float = 7.0
    # 会话 Cookie 是否带 Secure 位：本机 HTTP 演示必须为 false，
    # 公网 HTTPS 部署务必置 true（否则 Cookie 会明文跑在 http 上）
    cookie_secure: bool = False
    # 生产环境保持 false：注册需邀请码，避免被脚本批量注册刷模型额度
    allow_open_signup: bool = False
    invite_code: str = ""
    # 每自然日可创建的任务数上限（模型额度按人计，不按次计）
    user_daily_job_limit: int = 10
    # 逗号分隔的管理员用户名（大小写不敏感）：注册/登录时命中即授予管理员位
    admin_usernames: str = ""
    # 运行时改写模型服务地址与 Key 的能力。默认关闭：
    # 未鉴权时可被用来把真实 API Key 外送到任意 baseUrl（SSRF + 密钥外泄）。
    # 只有确有需要才打开，且打开后仅管理员可用。
    allow_runtime_config: bool = False
    # 同时运行的 Agent 任务数上限：每个任务内部还有 listing/图像并发，
    # 多任务叠加易触发模型限流；超出上限的任务排队等待（状态保持 queued）
    job_max_concurrency: int = 2
    # 排队 + 运行中的任务总数上限，超出直接拒绝（429）。
    # 排队任务同样占内存（每条任务记录带着用户实拍图的 base64），必须封顶
    job_max_live: int = 8
    # 单个素材包 zip 的体积上限，超出则拒绝打包（413）。
    # 打包已改为落盘 + 流式发送，不再吃进程内存，所以这里守的是磁盘与下载时长：
    # 一次 4 平台任务里光 2000px 的 PNG 就有 9 张，实测轻松上百 MB，别把上限压太紧
    zip_max_mb: int = 1024

    # 产物清理：任务结束（含重启遗留的孤儿目录）超过 TTL 后删除，
    # 每 cleanup_interval_minutes 分钟执行一轮；设为 0 可禁用
    cleanup_ttl_hours: float = 24.0
    cleanup_interval_minutes: float = 30.0

    # 阿里云 OSS：**可选的产物镜像**，默认关。产物一律以本地 output/ 为准，
    # 下载走本服务的 zip；它不是图片公网地址的来源，卖家不需要为此准备图床。
    oss_enabled: bool = False
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    oss_bucket: str = ""
    oss_endpoint: str = ""

    # 逗号分隔的受信反代地址。只有直连对端命中这里，才相信它传来的 X-Forwarded-For。
    # 同机 nginx 保持默认即可；反代在另一台机器/容器时填它的地址。
    trusted_proxies: str = "127.0.0.1,::1"
    # 同时打包下载的请求数。打包是纯 CPU + 磁盘写，单 worker 下几路并发下载就能把
    # 响应拖住，超出直接 429（比让所有人一起变慢好）
    download_max_concurrency: int = 2

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def trusted_proxy_set(self) -> tuple[str, ...]:
        return tuple(o.strip() for o in self.trusted_proxies.split(",") if o.strip())

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
