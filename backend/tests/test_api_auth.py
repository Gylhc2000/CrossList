"""鉴权与账号接口的真实往返（TestClient + 临时库，不联网）

这一组用例来自一次实测发现的严重缺陷：改口令的处理函数被写成 `async def` 之后交给
`asyncio.to_thread`，得到的是一个没人 await 的协程 —— 接口 500、改密 SQL 从未执行，
而逐行审查完全看不出来。凡是"只有跑起来才暴露"的路径，都在这里钉住。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

P1 = "OldPass!111"
P2 = "NewPass!222"
USER = "smoke_user"


@pytest.fixture(scope="module")
def client():
    """进一次 lifespan（app.state 上的 db / jobs 在这里初始化），整模块共用"""
    with TestClient(app) as c:
        yield c


def jar() -> TestClient:
    """另一个 cookie jar：用来模拟第二台设备上的会话"""
    return TestClient(app)


def register(name, password=P1, invite="test-invite-code"):
    return jar().post("/api/auth/register",
                      json={"username": name, "password": password, "invite": invite})


def test_register_requires_invite_and_grants_admin_on_empty_db(client):
    assert client.post("/api/auth/register",
                       json={"username": "no_invite", "password": P1, "invite": "错的"}
                       ).status_code == 403
    r = client.post("/api/auth/register", json={"username": USER, "password": P1,
                                                "invite": "test-invite-code"})
    assert r.status_code == 200
    # 空库时第一个账号成为管理员，是本项目刻意的规则
    assert r.json()["user"]["isAdmin"] is True


@pytest.mark.parametrize("bad", ["短", "weak_pw_user12345", "x" * 129])
def test_weak_passwords_rejected_at_register(bad):
    # 第二条同时命中"口令不得包含用户名"，所以用户名要和口令配套
    assert register("weak_pw_user", password=bad).status_code == 400


def test_protected_routes_require_session():
    # 必须用一个从没拿到过 Cookie 的 client：注册会顺带签发会话，
    # 复用模块级 client 会让它已经是登录态
    anon = jar()
    for path in ["/api/me", "/api/me/jobs", "/api/meta", "/api/jobs/whatever"]:
        assert anon.get(path).status_code == 401, path
    assert anon.get("/api/health").status_code == 200      # 部署排障要能匿名看 uptime
    assert anon.get("/api/auth/status").status_code == 200


def test_login_lockout_after_repeated_failures(client):
    codes = [client.post("/api/auth/login",
                         json={"username": "ghost_user", "password": "whatever-1"}).status_code
             for _ in range(10)]
    assert codes[:8] == [401] * 8            # 用户名不存在也回 401，不泄露"有没有这个账号"
    assert set(codes[8:]) == {429}


def test_password_change_updates_credential_and_revokes_other_sessions(client):
    assert client.post("/api/auth/login", json={"username": USER, "password": P1}).status_code == 200
    other = jar()
    assert other.post("/api/auth/login", json={"username": USER, "password": P1}).status_code == 200

    r = client.post("/api/me/password", json={"currentPassword": "不对的口令123", "newPassword": P2})
    assert r.status_code == 401
    assert client.get("/api/me").status_code == 200        # 改错了也不影响现有会话

    r = client.post("/api/me/password", json={"currentPassword": P1, "newPassword": P1})
    assert r.status_code == 400                            # 与旧口令相同

    r = client.post("/api/me/password", json={"currentPassword": P1, "newPassword": P2})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    # 只留当前这条会话；此前用例已经建过会话，这里只断言"确实吊销了别的"
    assert r.json()["revokedSessions"] >= 1

    assert client.get("/api/me").status_code == 200        # 当前会话不被踢
    assert other.get("/api/me").status_code == 401         # 另一条立刻失效（被盗会话的止损）
    assert jar().post("/api/auth/login",
                      json={"username": USER, "password": P1}).status_code == 401
    assert jar().post("/api/auth/login",
                      json={"username": USER, "password": P2}).status_code == 200


def test_cross_site_write_is_blocked(client):
    assert register("csrf_user").status_code == 200
    c = jar()
    assert c.post("/api/auth/login", json={"username": "csrf_user", "password": P1}).status_code == 200
    assert c.post("/api/jobs", json={"productName": "x", "platforms": ["amazon"]},
                  headers={"Origin": "http://evil.example"}).status_code == 403
