"""测试会话级环境

必须在**任何 app.* 被 import 之前**把环境变量摆好：`app.main` 在模块级别就
`create_app()` → `get_settings()`（lru_cache），晚设就晚了。
数据文件一律落在系统临时目录，不碰仓库里的 backend/data/crosslist.db。
"""
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="crosslist_pytest_"))

os.environ.setdefault("DATA_DIR", str(_TMP / "test.db"))
os.environ.setdefault("OUTPUT_DIR", str(_TMP / "output"))
os.environ.setdefault("LLM_API_KEY", "test-key-not-real")
os.environ.setdefault("INVITE_CODE", "test-invite-code")
os.environ.setdefault("ALLOW_OPEN_SIGNUP", "false")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("BACKEND_HOST", "127.0.0.1")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
