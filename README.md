# 跨境智上Agent（CrossList AI）

一键多平台商品上架素材智能体 —— 从商品原始信息到 Amazon / AliExpress / Shopee / TikTok Shop 批量上传素材包的全链路自动化。

## 技术选型

| 层 | 方案选型 | 本项目实现 |
|---|---|---|
| Agent 框架 | LangGraph 构建 Plan-and-Execute 工作流 | ✅ `backend/app/agent/graph.py`，含条件回边 |
| 后端 | Python + FastAPI | ✅ `backend/app/main.py` |
| 前端 | Gradio / Streamlit | ⚠️ **替换为 Next.js 14 + TypeScript**（原型图为 4 屏精细化交互，Gradio/Streamlit 无法还原） |
| 格式生成 | openpyxl（Amazon xlsx）/ pandas（CSV） | ✅ `backend/app/services/exporters/` |
| 存储 | 阿里云 OSS | ✅ 本地 `output/` 为准，OSS 为**可选镜像**（`OSS_ENABLED`，默认关）。产物一律走本服务 zip 下载，不要求卖家自备图床 |
| 部署 | 阿里云百炼平台 + ECS | 预留 |

## 快速开始

```bash
# 1) 后端（Python 3.11+）
cd backend
pip install -r requirements.txt      # 国内建议加 -i https://pypi.tuna.tsinghua.edu.cn/simple
cp .env.example .env                 # 填写 LLM_API_KEY 与 INVITE_CODE
python -m uvicorn app.main:app --port 8000

# 2) 前端（Node 18+）
cd frontend
npm install
npm run dev                          # http://localhost:3000
```

**首次使用**：注册需要 `.env` 里的 `INVITE_CODE`；空库时**第一个注册的账号自动成为管理员**（也可用 `ADMIN_USERNAMES` 指名）。
账号、会话与生成记录存在 `backend/data/crosslist.db`（标准库 sqlite3，无需装数据库服务）。

**测试**：`cd backend && pip install -r requirements-dev.txt && python -m pytest`。
`tests/` 覆盖的是不需要真实模型 Key 就能判定的部分（规则计量、模板列对齐、SKU 与图片包命名、口令/限速/脱敏）。
仓库里的 `backend/_test_*.py` 是**手工探针**（需要真实网关 Key，例如 i2i 效果、提示词字数标定），不参与 pytest，也不该在 CI 里跑。

## 目录结构

```
backend/
  app/
    main.py                FastAPI 入口 + CORS + 跨站写请求拦截 + 启动自检
    core/config.py         pydantic-settings 配置（读 .env）
    core/auth.py           scrypt 口令散列、会话令牌、登录限速、鉴权依赖
    core/redact.py         对外错误文案脱敏（网关地址、路径、凭据形态）
    core/audit.py          审计日志（注册/登录/改密/配置写回/建任务/删除）
    core/llm.py            OpenAI 兼容协议客户端（chat / images）
    rules/platforms.py     平台规则库（含规则核对日期）+ 批量上传模板字段
    agent/
      state.py             AgentState（LangGraph 状态）
      emitter.py           进度推送器（每订阅者一条有界队列）
      graph.py             ★ Plan-and-Execute 状态图 + 条件边
      nodes/
        plan.py            Orchestrator 任务规划（LLM + 规则兜底）
        parse.py           商品信息解析（多模态，失败降级）
        listing.py         多语言 Listing 生成（并发 3）
        images.py          主图 + 详情页生成（并发 4，失败降级占位图）
        validate.py        规则校验 + 语言质量评分
        fix.py             不合规自动修正（最多 2 轮）
        export.py          交付物生成：模板 / 上架对照表 / 图片包 / 报告
    services/
      db.py                SQLite：账号 / 会话 / 任务归属与历史
      jobs.py              任务管理（含每人日额度）/ 打包到临时文件
      storage.py           本地 output/ 为准，OSS 仅作可选镜像（读写均走线程池）
      imagefit.py          按平台尺寸适配 + PNG→JPEG 转码（图片包用）
      cleanup.py           产物 TTL 清理 + 单任务删除
      exporters/amazon.py  openpyxl 生成 Flat File
      exporters/csv_exporter.py  pandas 生成 CSV
      exporters/worksheet.py  上架对照表（一字段一行，含状态/计量）
    api/                   auth / meta / config / jobs 四个路由
  tests/                   pytest：规则计量、交付物结构、鉴权与账号接口的真实往返
frontend/
  app/page.tsx             登录门 + 4 屏容器（输入 / 进度 / 预览 / 下载）
  components/
    AuthView.tsx           登录 / 注册（邀请码）
    PasswordModal.tsx      自助改口令（改完吊销该账号其它会话）
    HistoryModal.tsx       我的生成记录（服务端数据源，可删除）
    InputView / ProgressView / PreviewView / DownloadView
    ConfigModal.tsx        模型配置（默认隐藏，需 ALLOW_RUNTIME_CONFIG + 管理员）
    ui.tsx                 Icon / Modal / Toast / ScoreRing 等基础件
  lib/api.ts               后端接口封装（统一带 credentials）
  lib/types.ts             TypeScript 类型
```

## Agent 工作流（LangGraph）

```
plan ──► parse ──┬─► listing ──┐
                 │             ├──► validate ──┬─► fix ─┐（条件回边，≤2 轮）
                 └─► image  ───┘               │        │
                                               │◄───────┘
                                               └─► export ──► END
```

- `listing` 与 `image` 都只依赖知识卡片，**并行执行**；关闭图像生成时 `image` 节点自行跳过
- `route_after_validate`：任一单元存在 error 级不合规且未达重试上限 → `fix`；否则 → `export`
- `recursion_limit=20` 防止死循环

访问 `http://localhost:8000/api/meta` 可拿到图的 Mermaid 描述，直接贴进方案文档。

## 主要接口

除 `/api/health` 与 `/api/auth/*` 外，全部接口都需要登录（HttpOnly 会话 Cookie），且任务类接口按账号隔离归属。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/auth/status` | 注册是否需邀请码（供登录页渲染） |
| POST | `/api/auth/register` | 邀请码注册，签发会话 Cookie |
| POST | `/api/auth/login` | 登录（失败 8 次锁 15 分钟） |
| POST | `/api/auth/logout` | 注销当前会话 |
| GET | `/api/me` | 当前账号与今日额度 |
| POST | `/api/me/password` | 修改口令（同时吊销该账号的其他会话） |
| GET | `/api/me/jobs` | 我的生成记录（跨重启存活） |
| DELETE | `/api/me/jobs/{id}` | 删除记录与其产物 |
| GET | `/api/meta` | 模型清单 / 市场 / 平台规则 / 图结构 |
| GET / PUT | `/api/config` | 读 / 写配置（写回需 `ALLOW_RUNTIME_CONFIG` + 管理员） |
| POST | `/api/test` | 模型连通性测试（同上管控） |
| POST | `/api/jobs` | 创建任务（受每人日额度约束） |
| GET | `/api/jobs/{id}` | 任务快照（内存没有则由 SQLite 存档重建） |
| GET | `/api/jobs/{id}/events` | SSE 进度流 |
| POST | `/api/jobs/{id}/cancel` | 取消任务 |
| GET | `/api/jobs/{id}/files` | 产物清单 |
| GET | `/api/jobs/{id}/asset?p=images/xx.png` | 素材图 |
| GET | `/api/jobs/{id}/download?scope=all\|amazon` | zip 下载（流式） |

## 说明

- 每个平台目录内含：Listing 文案 `.txt`、批量上传模板（xlsx/csv）、**上架对照表** `.xlsx`（一字段一行 + 状态/计量，给在网页后台逐个表单上架的卖家直接复制）、质量报告 `.md`，以及按该平台尺寸适配好的 `images/`。
- 平台批量上传模板为**简化演示版**（Amazon 40 列 / AliExpress 20 列 / Shopee 20 列 / TikTok 18 列），字段定义集中在 `backend/app/rules/platforms.py`。
  ⚠️ **列名相似 ≠ 平台能导入**：真实的批量上传模板需登录后台、按类目动态生成（Amazon 是多 sheet 且 `Template` 页有三行表头；Shopee 是 6 个子表且禁改列序），类目还要平台签发的数字 ID。当前导出物的定位是**素材生成器**，不是"导入即上架"。
  图片列一律**留空**：包内相对路径既不是平台可抓取的 URL、也不被当作文件名识别，填了只会让该列校验报错。可用路径有两条 —— Amazon 用 `Amazon_图片包_*.zip`（根目录文件名即 `<SKU>.MAIN.jpg` / `<SKU>.PT01…PT08.jpg`，后台 Catalog·Images·Upload images 直接收，**不需要 URL**）；其余平台把 `images/` 里的图拖进后台媒体库（Shopee 媒体空间 / 速卖通图片银行 / TikTok 素材库）后回填 URL。卖家仍需补的字段逐条列在《上架对照表》与质量报告末尾的「上传前必填」区块，预览页也会显示。
- 市场选择**不做平台拦截**：`MARKETS` 目前覆盖 美国 / 韩国 / 巴西 / 日本 / 德国 / 西班牙 六个市场，每个平台的"真实站点清单"（`Platform.markets`，来源为公开资料、待官方复核）只用于提示——勾了该平台没有自营站点的组合时照常生成，但会在进度告警、质量报告和输入页同时标注"仅作素材参考，不可直接上架"。要扩市场（如 Shopee 的东南亚与台湾站）需同时补语言与书写系统白名单。
- 未启用 LangGraph checkpointer：state 中含 `emitter` / `llm` / `storage` 等运行期对象，序列化成本高；条件回边能力已完整保留。
- `demo/` 为早期 Node.js 单进程版本，保留作对照，新开发请用 `backend/` + `frontend/`。
