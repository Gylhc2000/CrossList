# 部署指南（GitHub → 服务器）

本文档覆盖：推送代码到 GitHub、在服务器上配置并启动前后端、用 IP 地址访问、以及前后端分机时的连接方式。

---

## 0. 架构与端口

本项目是**前后端分离**的两个进程：

| 进程 | 技术栈 | 端口 | 说明 |
|---|---|---|---|
| 后端 | FastAPI + LangGraph | 8000 | 只提供 `/api/*` 接口，**任务状态存在进程内存里** |
| 前端 | Next.js 14 | 3000 | 页面 + 把 `/api/*` 交给后端 |

前端代码里**没有任何硬编码的后端地址**——`frontend/lib/api.ts` 全部使用相对路径 `/api/...`（连 SSE 的 `streamUrl` 也是）。所以「前端怎么连后端」这件事，只需要决定**谁来做 `/api` 的转发**。

### 两种方案对比

| | **方案 A：同机 nginx 单入口（推荐）** | **方案 B：前端在 Vercel，后端在服务器** |
|---|---|---|
| 结构 | nginx:80 → `/` 转 Next:3000<br>→ `/api` 转 FastAPI:8000 | Vercel 托管前端 → 通过 `http://IP:8000` 访问后端 |
| 浏览器访问 | `http://<服务器IP>/` | `https://xxx.vercel.app` |
| 跨域 CORS | 不需要（同源） | 不需要（Vercel 服务端代理），但要开公网 8000 |
| SSE 进度流 | 正常 | ⚠️ 可能被 Serverless 函数超时掐断 |
| 需要改代码 | **不需要** | 不需要，但要设 `BACKEND_ORIGIN` |
| 需要域名 | 不需要 | 不需要（但见下方限制） |
| 适用 | 演示 / 自用 / 没有域名 | 想用 Vercel 的免费托管 |

> **结论：演示场景请用方案 A。** 本项目进度页靠 SSE 长连接推送（一次生成可能跑几分钟），Serverless 平台的函数执行时长限制（Vercel Hobby 默认 10s）会把这条连接掐掉，表现为**进度条走到一半不动了**。

---

## 1. 推送到 GitHub

密钥已在 `.gitignore` 里排除，直接推是安全的。

```bash
cd D:/ProjectWorkBuddy/CrossListAI

# 首次关联远程（仓库先在 GitHub 上建好，不要勾选 README/gitignore）
git remote add origin https://github.com/<你的账号>/<仓库名>.git
git branch -M main
git push -u origin main
```

推送后可自查一遍密钥确实没上去：

```bash
git remote -v                                  # 确认远程地址
git ls-files | grep -i "\.env"                 # 只应出现 backend/.env.example
```

> `backend/.env`（真实 API Key）**不会**进仓库，服务器上需要单独创建，见第 3 步。

---

## 2. 服务器环境准备

以 Ubuntu 22.04 / 24.04 为例（CentOS 系把 `apt` 换 `dnf`、把 `ufw` 换 `firewall-cmd`）。

```bash
# ---- 系统依赖 ----
sudo apt update
sudo apt install -y git nginx python3 python3-venv python3-pip curl

# Python 版本需 >= 3.11；Ubuntu 24.04 自带 3.12。
# 若是 22.04（自带 3.10），补装新版：
#   sudo add-apt-repository -y ppa:deadsnakes/ppa
#   sudo apt install -y python3.12 python3.12-venv

# ---- Node.js 20 LTS ----
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
node -v && npm -v        # 需 >= 18.17，Next 14 的硬性要求

# ---- 专用服务账号（不要用 root 跑业务）----
sudo useradd -r -m -s /bin/bash crosslist
sudo mkdir -p /var/log/crosslist
sudo chown -R crosslist:crosslist /var/log/crosslist

# ---- 防火墙：只放行 80（方案 B 再加上 8000）----
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp       # 之后想上 HTTPS 用
# sudo ufw allow 8000/tcp    # ← 仅方案 B 需要
sudo ufw enable
```

> ⚠️ **云服务器还要去控制台放行安全组**（阿里云/腾讯云/华为云的「入方向规则」）。只在系统里配 ufw 而没开安全组，外网依然访问不通——这是最常见的「明明服务起来了却打不开」。

拉代码：

```bash
cd /opt
sudo git clone https://github.com/<你的账号>/<仓库名>.git crosslist
sudo chown -R crosslist:crosslist /opt/crosslist
```

> 仓库根目录会变成 `/opt/crosslist`，于是后端在 `/opt/crosslist/backend`、前端在 `/opt/crosslist/frontend`、部署文件在 `/opt/crosslist/deploy`——与后文 systemd 单元里的路径完全对应。

---

## 3. 后端：装依赖 + 配置

```bash
cd /opt/crosslist/backend

# 虚拟环境（用 python3.12 替换 python3 如果装了新版）
sudo -u crosslist python3 -m venv .venv
sudo -u crosslist .venv/bin/pip install -U pip
sudo -u crosslist .venv/bin/pip install -r requirements.txt
# requirements.txt 只写版本下限。发布前在**这台机器上**冻结一份锁定清单入库，
# 之后所有环境与 CI 都装它，避免"昨天还能跑"：
#   .venv/bin/pip freeze > requirements.lock     然后改用 -r requirements.lock

# 部署前自检（不需要 Key，30 秒内跑完）
sudo -u crosslist .venv/bin/pip install -r requirements-dev.txt
sudo -u crosslist .venv/bin/python -m pytest
# 覆盖：规则计量、模板列与数据行对齐、SKU 与图片包命名、口令散列、
# 登录限速（含伪造 X-Forwarded-For 绕不开锁定）、错误脱敏、鉴权接口往返

# 生成配置文件（模板在 deploy/ 下，已入库且不含真实密钥）
sudo -u crosslist cp /opt/crosslist/deploy/backend.env.production.example .env
sudo -u crosslist nano .env        # ← 填 LLM_API_KEY
```

`.env` 里至少要确认这几项：

```ini
LLM_API_KEY=<你的真实密钥>
LLM_TEXT_MODEL=deepseek-v4-flash
CORS_ORIGINS=http://localhost:3000     # 方案 A 用不到；方案 B 要填前端真实来源
```

然后收紧权限：

```bash
sudo chown crosslist:crosslist /opt/crosslist/backend/.env
sudo chmod 600 /opt/crosslist/backend/.env
```

> **为什么必须是宿主 crosslist 且可写？**
> 前端「⚙ 模型配置」保存时会调用 `PUT /api/config`，该接口会**直接改写这个 `.env` 文件**。
> 如果 `.env` 只有 root 可写，页面上保存模型配置会直接 500。
> 另外 `chmod 600` 是为了防止同机其它账号读到密钥。

---

## 4. 前端：安装 + 构建

```bash
cd /opt/crosslist/frontend

sudo -u crosslist npm ci            # 用 package-lock.json 精确还原依赖

# 仅方案 B（前端与后端不在同一台机器）需要：
# sudo -u crosslist env BACKEND_ORIGIN=http://<后端IP>:8000 npm run build

sudo -u crosslist npm run build
ls .next/BUILD_ID                   # 有输出才说明构建成功
```

> ⚠️ **`next start` 不会编译**。必须先 `npm run build` 产出 `.next/`，否则服务一起来就退出。
> ⚠️ **`BACKEND_ORIGIN` 是构建期变量**：`rewrites` 会被固化进 `.next/routes-manifest.json`，构建之后再改环境变量、只重启进程是**无效**的，必须重新 build。方案 A 下 `/api` 被 nginx 直接接管，这个变量根本不参与请求链路。
> ⚠️ 构建约占 1GB 内存，小内存 VPS 会 OOM Killed，先加 2G swap 再 build。

---

## 5. 用 systemd 启动两个服务

```bash
sudo cp /opt/crosslist/deploy/crosslist-backend.service  /etc/systemd/system/
sudo cp /opt/crosslist/deploy/crosslist-frontend.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now crosslist-backend
sudo systemctl enable --now crosslist-frontend

# 看状态与日志
systemctl status crosslist-backend --no-pager
journalctl -u crosslist-backend -n 50 --no-pager -f
```

两个单元文件的关键设定（详见 `deploy/` 目录内注释）：

- **后端 `--workers 1`**：任务状态和 SSE 订阅者都在进程内存里，开多 worker 会出现「任务在 A 创建、请求落到 B → 404」。需要更高并发得先把状态外置到 Redis。
- **生产不要加 `--reload`**。
- 后端绑 `127.0.0.1:8000`、前端绑 `127.0.0.1:3000`，**都不直接暴露公网**，统一由 nginx 收口。

---

## 6. nginx 反向代理（方案 A 的核心）

```bash
# 先干掉占着 80 端口的默认站点，否则它 server_name _ 会跟我们的配置抢请求
sudo rm -f /etc/nginx/sites-enabled/default

sudo cp /opt/crosslist/deploy/nginx-crosslist.conf /etc/nginx/conf.d/crosslist.conf
sudo nginx -t                 # 语法自检，必须 successful
sudo systemctl reload nginx
```

这份配置做了三件事：

1. `/api/` → `127.0.0.1:8000`（FastAPI），**`proxy_pass` 结尾不带斜杠**，保留 `/api` 前缀
2. `/` → `127.0.0.1:3000`（Next.js）
3. 对 `/api/` 关闭 `proxy_buffering` 并把超时提到 3600s —— 保证 SSE 进度流实时下发

> **最容易踩的坑**：`proxy_pass http://127.0.0.1:8000/;` 结尾多一个斜杠，nginx 会把 `/api` 前缀剥掉，后端收到 `/jobs` → **全部 404**。配置文件里已注明。

---

## 7. 验证

```bash
# 后端活着吗（同时能看到加载的模型）
curl -s http://127.0.0.1:8000/api/health
# → {"ok":true,"hasKey":true,"model":"deepseek-v4-flash","started_at":"...","uptime_s":12}
#   hasKey 必须是 true，否则 .env 没生效

# 前端活着吗
curl -sI http://127.0.0.1:3000 | head -1        # HTTP/1.1 200 OK

# 经 nginx 走一遍（这才是外网的真实链路）
curl -s http://127.0.0.1/api/health

# 外网
curl -sI http://<服务器IP>/ | head -1
```

浏览器打开 `http://<服务器IP>/`，用一个小商品跑一遍任务，重点确认**进度页的日志和阶段是实时跳变的**（若卡住不动，就是 SSE 缓冲问题，回看第 6 步）。

---

## 8. 方案 B：前端在 Vercel，后端在服务器 IP

1. **后端必须能被公网访问**：把单元文件的 `--host 127.0.0.1` 改成 `--host 0.0.0.0`，然后放行 8000：

   ```bash
   sudo ufw allow 8000/tcp        # 别忘了云控制台安全组也要开
   curl -s http://<服务器IP>:8000/api/health   # 从你本机测试
   ```

2. **Vercel 侧**：导入 GitHub 仓库 → Root Directory 设为 `frontend` → 加环境变量
   `BACKEND_ORIGIN = http://<服务器IP>:8000`（Vercel 在构建前注入，因此构建期固化是正常生效的）。

3. **CORS**：走 Vercel 服务端代理时浏览器同源，理论上不需要；但如果你改成浏览器直连后端，就必须在服务器 `.env` 里放行前端来源：

   ```ini
   CORS_ORIGINS=http://1.2.3.4,https://your-app.vercel.app
   ```

4. **两个实际限制，建议先想清楚**：
   - **SSE 会被掐断**：`/api/jobs/{id}/events` 是长连接，Vercel Serverless 的函数时长上限（Hobby 10s）会让进度流中断。除非把进度改为轮询。
   - **浏览器不能直连 `http://IP:8000`**：页面是 HTTPS，请求 HTTP 接口属于混合内容，会被浏览器直接拦掉。要直连就得给后端配**域名 + 证书**。所以方案 B 只能走「Vercel 服务端代理」这条路，也就绕不开上面那条超时限制。

---

## 9. 日常更新流程

```bash
cd /opt/crosslist
sudo -u crosslist git pull

# 后端：依赖有变化才需要重装
sudo -u crosslist backend/.venv/bin/pip install -r backend/requirements.txt
sudo systemctl restart crosslist-backend

# 前端：改过代码就必须重新 build
cd frontend
sudo -u crosslist npm ci
sudo -u crosslist npm run build
sudo systemctl restart crosslist-frontend
```

---

## 10. 上线前必查的安全项

服务一旦公网可达，扫描器会在几小时内找到它。下面每条都是发布前必须确认的：

1. **`INVITE_CODE` 必须设，`ALLOW_OPEN_SIGNUP` 保持 false**。模型 Key 是全站共享的一把，开放注册等于任何人都能注册后用你的额度批量生成（一次任务 ≈ 13 次对话 + 9 次图像调用）。有邀请码你才握有发放权。生成一个：`openssl rand -hex 16`。
2. **`COOKIE_SECURE=true`**（HTTPS 下）。设 false 时启动日志会告警；会话 Cookie 没有 Secure 位 = 同网段可嗅探。
3. **`ALLOW_RUNTIME_CONFIG` 保持 false**。开着时 `POST /api/test` 会按请求里的 `baseUrl` 发真实请求，且 `apiKey` 缺省会带上服务端真实 Key —— 一次带回显的 SSRF + 密钥外泄。现在它另外还要求管理员身份，但默认关才对。
4. **`BACKEND_HOST` 保持 `127.0.0.1`**，公网入口只留 nginx 的 80/443。下文方案 B 那种"放行 8000 直连后端"的写法在没有鉴权层的暴露面上更宽，慎用。
5. **`ADMIN_USERNAMES` 填你自己的用户名**。空库时第一个注册者会自动成为管理员，但这个隐式规则可能被"先注册的评委"撞上。
6. **文件权限**：`backend/.env` 设 `chmod 600`；`data/`（SQLite）与 `output/` 归服务账号所有。
7. **`TRUSTED_PROXIES` 只填 nginx 的 IP**（默认 `127.0.0.1,::1`）。登录限速按 `(来源IP, 用户名)` 计；来源 IP 现在会取 `X-Forwarded-For`，但**仅当直连对端在这个名单里**，否则忽略头、用真实对端地址——这样隔着 nginx 仍能区分不同机器，而直连后端的攻击者也无法靠伪造 XFF 绕开锁定。填 `*` 启动日志会告警，等于把 IP 判定权交给任意请求方。
8. **uvicorn 的 `--forwarded-allow-ips` 要和 `TRUSTED_PROXIES` 一致**。`deploy/crosslist-backend.service` 已写成 `127.0.0.1`；改成 `'*'` 会让 `request.client.host` 直接采信伪造头，绕过上一条。
9. **改口令只留当前会话**。`POST /api/me/password` 会吊销该账号的其他所有会话，Cookie 被复制走的场景下这是唯一的止损入口。

---

## 11. 本项目特有的坑（重要）

1. **必须单 worker**：JobManager 是内存态，多 worker → 运行中任务查询 404。账号/会话/历史已在 SQLite 里，但**执行中的任务状态与 SSE 订阅仍在进程内存**，所以 worker 只能 1。
2. **后端重启不再丢历史**：账号、生成记录与已完成任务的完整结果都存在 SQLite，重启后登录回来仍能看到并下载（产物文件另有 24h TTL，过期条目会标"产物已过期"）。**运行中的任务会中断**，并在下次启动时被统一改写成"服务重启导致任务中断"——历史列表里不会留下永远"运行中"的假记录。
3. **日志里出现旧配置**：先看 `/api/health` 的 `uptime_s`。若 uptime 早于你的修改时间，说明有**老进程仍在服务**（端口被旧实例占着、新实例绑定失败但假装成功了）。用 `netstat -ano | grep :8000`（Windows）或 `ss -lntp | grep 8000`（Linux）列出所有 PID 逐个清掉。
4. **`.env` 改了要重启**：`get_settings()` 是 `@lru_cache`，进程内只读一次；`--reload` 也不监控 `.env`。
5. **产物会吃满磁盘**：每次任务在 `backend/output/<jobId>/` 落图。内置清理任务默认 24 小时 TTL、30 分钟扫一轮，通过 `CLEANUP_TTL_HOURS` / `CLEANUP_INTERVAL_MINUTES` 调整（设 0 禁用）。磁盘紧张就把 `OUTPUT_DIR` 挂到数据盘。
6. **服务器要能出网**：生成依赖调用你在 `.env` 的 `LLM_BASE_URL` 里配的模型网关（百炼兼容端点；若用的是工作空间专属域名，别写进本文档，只写进 `.env`）。若服务器在无 NAT 的内网或安全组封了出方向，任务会直接失败在第一步。
7. **构建产物属主**：不要用 root 执行过 `npm run build` 再切 crosslist 去跑，会出现 `.next` 权限错误。全程统一用 `sudo -u crosslist`。
8. **上 HTTPS（可选）**：有了域名后 `sudo apt install certbot python3-certbot-nginx && sudo certbot --nginx -d your.domain`，certbot 会自动改写上面的 nginx 配置。
