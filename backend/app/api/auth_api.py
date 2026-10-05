"""账号接口：注册 / 登录 / 登出 / 我是谁 / 我的历史

注册默认需要邀请码（allow_open_signup=false），否则公网会被脚本批量注册
并用光同一把模型 Key 的额度。
"""
from __future__ import annotations

import asyncio
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.core.audit import audit
from app.core.auth import (
    SESSION_COOKIE,
    User,
    check_password_policy,
    check_username,
    dummy_verify,
    hash_password,
    login_throttle,
    new_session_token,
    require_user,
    signup_throttle,
    today_start,
    verify_password,
)
from app.services.cleanup import remove_job_output
from app.services.jobs import TERMINAL_STATUS

router = APIRouter(prefix="/api", tags=["auth"])

PUBLIC_FIELDS = ("jobCount", "platformNames", "marketLabels", "imageCount", "avgScore")


class Credentials(BaseModel):
    username: str = ""
    password: str = ""
    invite: str | None = None


def _settings(request: Request):
    return request.app.state.settings


def _db(request: Request):
    return request.app.state.db


def _admin_names(request: Request) -> set[str]:
    return {s.strip().lower() for s in _settings(request).admin_usernames.split(",") if s.strip()}


def _tp(request: Request) -> tuple[str, ...]:
    """限速计数要信任的反代列表。"""
    return _settings(request).trusted_proxy_set


def _session_hash(request: Request) -> str:
    token = request.cookies.get(SESSION_COOKIE) or ""
    return hashlib.sha256(token.encode()).hexdigest() if token else ""


def _issue(request: Request, response: Response, uid: int) -> None:
    s = _settings(request)
    token, _ = new_session_token()
    ttl = s.session_ttl_days * 86400
    _db(request).insert_session(hashlib.sha256(token.encode()).hexdigest(), uid, ttl,
                                request.headers.get("user-agent", ""))
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=int(ttl),
        httponly=True,            # 会话不给 JS 读，XSS 也带不走
        secure=s.cookie_secure,
        samesite="lax",           # 跨站 POST 不带 Cookie，等于自带 CSRF 防护
        path="/",
    )


def _payload(user: User) -> dict:
    return {"id": user.id, "username": user.username, "isAdmin": user.is_admin}


@router.get("/auth/status")
async def auth_status(request: Request):
    """前端据此决定注册表单是否要邀请码，不必把邀请码写进前端。"""
    s = _settings(request)
    count = await asyncio.to_thread(_db(request).user_count)
    return {
        "openSignup": bool(s.allow_open_signup),
        "inviteRequired": not s.allow_open_signup,
        "hasAccounts": count > 0,
    }


@router.post("/auth/register")
async def register(body: Credentials, request: Request, response: Response):
    s = _settings(request)
    username = check_username(body.username)
    check_password_policy(username, body.password)

    if not s.allow_open_signup:
        if not s.invite_code:
            raise HTTPException(403, "注册未开放：服务端未配置邀请码")
        if (body.invite or "").strip() != s.invite_code:
            raise HTTPException(403, "邀请码不正确")
    if signup_throttle.blocked_for(request, "signup", _tp(request)) > 0:
        raise HTTPException(429, "注册过于频繁，请稍后再试")
    signup_throttle.fail(request, "signup", _tp(request))   # 先记一次尝试，成功后再撤销

    def do_register() -> User | None:
        db = _db(request)
        if db.user_by_name(username):
            return None
        names = _admin_names(request)
        # 空库时第一个账号成为管理员，避免实例无人可管；配了名单就按名单走
        is_admin = username.lower() in names or (db.user_count() == 0 and not names)
        uid = db.insert_user(username, hash_password(body.password), is_admin)
        return None if uid is None else User(uid, username, is_admin)

    user = await asyncio.to_thread(do_register)
    if user is None:
        audit("register", request=request, ok=False, reason="taken", attempted=username)
        raise HTTPException(409, "该用户名已被占用")
    signup_throttle.clear(request, "signup", _tp(request))
    _issue(request, response, user.id)
    audit("register", request=request, user=user, admin=user.is_admin)
    return {"user": _payload(user)}


@router.post("/auth/login")
async def login(body: Credentials, request: Request, response: Response):
    username = (body.username or "").strip()
    password = body.password or ""
    wait = login_throttle.blocked_for(request, username, _tp(request))
    if wait > 0:
        audit("login", request=request, ok=False, reason="throttled", attempted=username)
        raise HTTPException(429, f"失败次数过多，请 {int(wait) + 1} 秒后重试")
    if not username or not password:
        raise HTTPException(400, "请填写用户名与口令")

    db = _db(request)
    row = await asyncio.to_thread(db.user_by_name, username)
    if not row:
        dummy_verify(password)     # 等量耗时：别用响应时间泄露账号是否存在
        login_throttle.fail(request, username, _tp(request))
        audit("login", request=request, ok=False, reason="unknown_user", attempted=username)
        raise HTTPException(401, "用户名或口令不正确")
    if not verify_password(password, row["pass_hash"]):
        login_throttle.fail(request, username, _tp(request))
        audit("login", request=request, ok=False, reason="bad_password", attempted=username)
        raise HTTPException(401, "用户名或口令不正确")

    user = User.from_row(row)
    # 管理员名单可以在建号之后再补，所以登录时也要认
    if not user.is_admin and user.username.lower() in _admin_names(request):
        await asyncio.to_thread(db.exec, "UPDATE users SET is_admin=1 WHERE id=?", (user.id,))
        user = User(user.id, user.username, True)
        audit("grant_admin", request=request, user=user)
    login_throttle.clear(request, username, _tp(request))
    _issue(request, response, user.id)
    audit("login", request=request, user=user)
    return {"user": _payload(user)}


@router.post("/auth/logout")
async def logout(request: Request, response: Response, user: User = Depends(require_user)):
    audit("logout", request=request, user=user)
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await asyncio.to_thread(_db(request).revoke_session,
                                hashlib.sha256(token.encode()).hexdigest())
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


class PasswordChange(BaseModel):
    currentPassword: str = ""
    newPassword: str = ""


@router.post("/me/password")
async def change_password(body: PasswordChange, request: Request, response: Response,
                          user: User = Depends(require_user)):
    """改口令，并踢掉该账号的其它会话。

    没有邮件通道，所以不提供"忘记口令"—— 忘口令只能管理员到服务器上手删。
    但改口令必须是自助可用的，否则被盗后用无损失止手段。
    """
    ident = f"pw|{user.id}"
    wait = login_throttle.blocked_for(request, ident, _tp(request))
    if wait > 0:
        raise HTTPException(429, f"失败次数过多，请 {int(wait) + 1} 秒后重试")

    row = await asyncio.to_thread(_db(request).user_by_id, user.id)
    if not row:
        raise HTTPException(401, "请先登录")
    if not verify_password(body.currentPassword or "", row["pass_hash"]):
        login_throttle.fail(request, ident, _tp(request))
        raise HTTPException(401, "当前口令不正确")
    try:
        check_password_policy(user.username, body.newPassword or "")
    except HTTPException:
        login_throttle.fail(request, ident, _tp(request))
        raise
    if (body.newPassword or "") == (body.currentPassword or ""):
        raise HTTPException(400, "新口令不能与当前口令相同")

    keep = _session_hash(request)

    # 注意这里必须是**同步**函数：asyncio.to_thread 只是把可调用对象丢进线程池，
    # 传 async def 进去得到的是个没人 await 的协程 —— 改密 SQL 根本不执行，
    # 返回值还是协程对象，序列化时才炸出 500（sqlite 连接跨线程也会是隐患）
    def apply():
        db = _db(request)
        db.update_password(user.id, hash_password(body.newPassword))
        # 当前这条会话保留，其余一律作废：这样"其它设备"上的旧会话立刻失效
        return db.revoke_other_sessions(user.id, keep) if keep else 0

    revoked = await asyncio.to_thread(apply)
    login_throttle.clear(request, ident, _tp(request))
    audit("password_change", request=request, user=user, revoked_sessions=revoked)
    return {"ok": True, "revokedSessions": revoked}


@router.get("/me")
async def me(request: Request, user: User = Depends(require_user)):
    s = _settings(request)
    used = await asyncio.to_thread(_db(request).count_jobs_since, user.id, today_start())
    return {
        **_payload(user),
        "quota": {"limit": s.user_daily_job_limit, "used": used,
                  "remaining": max(0, s.user_daily_job_limit - used)},
    }


@router.get("/me/jobs")
async def my_jobs(request: Request, limit: int = 50, offset: int = 0,
                  user: User = Depends(require_user)):
    """我的生成记录。跨重启存活：列表来自 SQLite，产物按 TTL 过期后仍能看到历史。"""
    rows = await asyncio.to_thread(
        _db(request).list_jobs, user.id, max(1, min(limit, 100)), max(0, offset))
    out = []
    for r in rows:
        try:
            summary = json.loads(r.get("summary_json") or "{}")
        except ValueError:
            summary = {}
        out.append({
            "jobId": r["id"],
            "productName": r["product_name"],
            "status": r["status"],
            "error": r.get("error"),
            "createdAt": r["created_at"],
            "finishedAt": r["finished_at"],
            "hasResult": bool(r["has_result"]),
            **{k: summary[k] for k in PUBLIC_FIELDS if k in summary},
        })
    return {"jobs": out, "limit": limit, "offset": offset}


@router.delete("/me/jobs/{job_id}")
async def delete_my_job(job_id: str, request: Request, user: User = Depends(require_user)):
    """删记录 + 删产物。产物目录可能已被 TTL 清掉，所以两者都容忍"本来就没有"。"""
    db = _db(request)
    rec = await asyncio.to_thread(db.job_record, job_id, user.id)
    if not rec:
        raise HTTPException(404, "记录不存在")
    live = request.app.state.jobs.get(job_id)
    if live and live.status not in TERMINAL_STATUS:
        raise HTTPException(409, "任务仍在执行，请先取消")
    await asyncio.to_thread(db.delete_jobs_of, [job_id])
    await asyncio.to_thread(remove_job_output, _settings(request).output_path, job_id)
    # 内存里那份也要摘掉，否则被删的 jobId 仍能取快照、仍能挂 SSE
    request.app.state.jobs.forget(job_id, "用户删除记录")
    return {"ok": True}
