"""SQLite 持久层：账号、会话、任务归属与历史

只用标准库 sqlite3，不引入 ORM/迁移框架：这张库很小（账号 + 任务索引），
建表语句即 schema，启动时幂等执行。

所有函数都是同步阻塞的。调用方（HTTP 层）必须用 asyncio.to_thread 包一层，
否则 SQLite 写锁会把事件循环卡住 —— 这与产物 IO 同一个道理。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.core.config import Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  username    TEXT    NOT NULL UNIQUE COLLATE NOCASE,
  pass_hash   TEXT    NOT NULL,
  is_admin    INTEGER NOT NULL DEFAULT 0,
  created_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash  TEXT    PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at  REAL    NOT NULL,
  expires_at  REAL    NOT NULL,
  user_agent  TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);

-- 任务索引：产物文件仍在 output/<job_id>/ 下按 TTL 清理，
-- 这张表只保证"我记得自己生成过什么"能跨重启存活。
CREATE TABLE IF NOT EXISTS jobs (
  id            TEXT    PRIMARY KEY,
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  product_name  TEXT    NOT NULL DEFAULT '',
  summary_json  TEXT    NOT NULL DEFAULT '{}',
  status        TEXT    NOT NULL DEFAULT 'queued',
  error         TEXT,
  created_at    REAL    NOT NULL,
  finished_at   REAL,
  -- 终态任务的完整 result（已剔除 base64 图），供服务重启后仍可预览/下载报告
  result_json   TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_user_time ON jobs(user_id, created_at DESC);
"""

# 单条 result_json 的写入上限：超出就只留摘要，避免小库被巨型任务撑爆
RESULT_JSON_MAX = 1_500_000

# 表结构版本。改表必须同时在 MIGRATIONS 里加一步，并把 SCHEMA_VERSION 递增。
SCHEMA_VERSION = 1

# 索引到 v+1 的迁移语句。v1 就是上面的 SCHEMA（全新库直接建表），所以这里为空。
# 后续加字段的形状：MIGRATIONS[1] = ["ALTER TABLE users ADD COLUMN ...", "PRAGMA user_version=2"]
MIGRATIONS: dict[int, list[str]] = {}


class SchemaTooNew(RuntimeError):
    """库的 schema 比当前代码还新：多半是回滚了版本或多实例共用了一份 DB"""


class Db:
    def __init__(self, settings: Settings):
        self.path = Path(settings.data_dir)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session_ttl = max(3600.0, settings.session_ttl_days * 86400)
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _migrate(self) -> None:
        conn = self._conn()
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise SchemaTooNew(
                    f"{self.path} 的 schema 版本为 {version}，当前代码只支持到 {SCHEMA_VERSION}；"
                    f"请勿用旧版本后端打开新库"
                )
            if version == 0:
                conn.executescript(SCHEMA)      # 新库：直接建到当前版本
            else:
                while version < SCHEMA_VERSION:
                    for sql in MIGRATIONS.get(version, []):
                        conn.execute(sql)
                    version += 1
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()
        finally:
            conn.close()

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._conn() as c:
            return list(c.execute(sql, args).fetchall())

    def q1(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def insert(self, sql: str, args: tuple = ()) -> int | None:
        conn = self._conn()
        try:
            cur = conn.execute(sql, args)
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def exec(self, sql: str, args: tuple = ()) -> int:
        """写操作（UPDATE/DELETE），返回受影响行数。"""
        conn = self._conn()
        try:
            cur = conn.execute(sql, args)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    # ---------------- 账号 ----------------
    def insert_user(self, username: str, pass_hash: str, is_admin: bool = False) -> int | None:
        """同名已在则返回 None（并发注册竞态由 UNIQUE 兜住）。"""
        try:
            return self.insert(
                "INSERT INTO users(username, pass_hash, is_admin, created_at) VALUES(?,?,?,?)",
                (username, pass_hash, 1 if is_admin else 0, time.time()),
            )
        except sqlite3.IntegrityError:
            return None

    def user_by_name(self, username: str) -> sqlite3.Row | None:
        return self.q1("SELECT * FROM users WHERE username = ?", (username,))

    def user_by_id(self, uid: int) -> sqlite3.Row | None:
        return self.q1("SELECT * FROM users WHERE id = ?", (uid,))

    def user_count(self) -> int:
        row = self.q1("SELECT COUNT(*) AS n FROM users")
        return int(row["n"]) if row else 0

    # ---------------- 会话 ----------------
    def insert_session(self, token_hash: str, uid: int, ttl_seconds: float, ua: str) -> None:
        now = time.time()
        self.exec(
            "INSERT INTO sessions(token_hash, user_id, created_at, expires_at, user_agent)"
            " VALUES(?,?,?,?,?)",
            (token_hash, uid, now, now + ttl_seconds, ua[:200]),
        )

    def session_user(self, token_hash: str) -> sqlite3.Row | None:
        """取会话对应用户，顺带滑动续期；过期即删。"""
        row = self.q1("SELECT * FROM sessions WHERE token_hash = ?", (token_hash,))
        if not row:
            return None
        if row["expires_at"] < time.time():
            self.exec("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            return None
        new_exp = time.time() + self.session_ttl
        self.exec("UPDATE sessions SET expires_at = ? WHERE token_hash = ?", (new_exp, token_hash))
        return self.user_by_id(row["user_id"])

    def revoke_session(self, token_hash: str) -> None:
        self.exec("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def update_password(self, uid: int, pass_hash: str) -> None:
        self.exec("UPDATE users SET pass_hash = ? WHERE id = ?", (pass_hash, uid))

    def revoke_other_sessions(self, uid: int, keep_token_hash: str) -> int:
        """换口令后踢掉该账号的其它设备，只留当前这条会话。

        不这么做的话，改密码对已经被偷走的会话毫无止损作用。
        """
        return self.exec(
            "DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?",
            (uid, keep_token_hash),
        )

    def purge_sessions(self) -> int:
        return self.exec("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))

    # ---------------- 任务索引 ----------------
    def insert_job(self, job_id: str, uid: int, product_name: str, summary: dict) -> None:
        self.exec(
            "INSERT OR REPLACE INTO jobs(id, user_id, product_name, summary_json, status, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (job_id, uid, product_name[:120], json.dumps(summary, ensure_ascii=False),
             "queued", time.time()),
        )

    def finish_job(self, job_id: str, status: str, error: str | None,
                   result: dict | None) -> None:
        blob: str | None = None
        if result:
            try:
                text = json.dumps(result, ensure_ascii=False, default=str)
                blob = text if len(text) <= RESULT_JSON_MAX else None
            except (TypeError, ValueError):
                blob = None
        self.exec(
            "UPDATE jobs SET status=?, error=?, finished_at=?, result_json=COALESCE(?, result_json)"
            " WHERE id=?",
            (status, (error or "")[:500] or None, time.time(), blob, job_id),
        )

    def abandon_unfinished(self) -> int:
        """把上一次进程遗留的 queued/running 记录收敛成 error。

        任务的真实状态活在那个已经死掉的进程里，再没人来写终态：不收口的话历史
        列表上会永远挂着一条"运行中"，点进去还会去接一个不存在的事件流。
        """
        return self.exec(
            "UPDATE jobs SET status='error', error=?, finished_at=?"
            " WHERE status IN ('queued','running')",
            ("服务重启导致任务中断，请重新运行", time.time()),
        )

    def job_owner(self, job_id: str) -> int | None:
        row = self.q1("SELECT user_id FROM jobs WHERE id = ?", (job_id,))
        return int(row["user_id"]) if row else None

    def list_jobs(self, uid: int, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return [dict(r) for r in self.q(
            "SELECT id, product_name, summary_json, status, error, created_at, finished_at,"
            " (result_json IS NOT NULL) AS has_result"
            " FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (uid, limit, offset),
        )]

    def job_record(self, job_id: str, uid: int) -> dict[str, Any] | None:
        row = self.q1(
            "SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, uid))
        return dict(row) if row else None

    def count_jobs_since(self, uid: int, since: float) -> int:
        row = self.q1(
            "SELECT COUNT(*) AS n FROM jobs WHERE user_id=? AND created_at >= ?",
            (uid, since))
        return int(row["n"]) if row else 0

    def delete_jobs_of(self, job_ids: list[str]) -> int:
        # 只删记录，产物目录由 services.cleanup 负责
        marks = ",".join("?" * len(job_ids))
        return self.exec(f"DELETE FROM jobs WHERE id IN ({marks})", tuple(job_ids))
