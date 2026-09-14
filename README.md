# 跨境智上Agent（CrossList AI）

一键多平台商品上架素材智能体 —— 从商品原始信息到 Amazon / AliExpress / Shopee / TikTok Shop 批量上传素材包的全链路自动化。

## 技术选型（对齐《方案概述》4.4）

| 层 | 方案选型 | 本项目实现 |
|---|---|---|
| Agent 框架 | LangGraph 构建 Plan-and-Execute 工作流 | ✅ `backend/app/agent/graph.py`，含条件回边 |
| 后端 | Python + FastAPI | ✅ `backend/app/main.py` |
| 前端 | Gradio / Streamlit | ⚠️ **替换为 Next.js 14 + TypeScript**（原型图为 4 屏精细化交互，Gradio/Streamlit 无法还原） |
| 格式生成 | openpyxl（Amazon xlsx）/ pandas（CSV） | ✅ `backend/app/services/exporters/` |
| 存储 | 阿里云 OSS | ✅ `backend/app/services/storage.py`（`OSS_ENABLED=false` 时回落本地 `output/`） |
| 部署 | 阿里云百炼平台 + ECS | 预留 |

## 快速开始

```bash
# 1) 后端（Python 3.11+）
cd backend
pip install -r requirements.txt      # 国内建议加 -i https://pypi.tuna.tsinghua.edu.cn/simple
cp .env.example .env                 # 填写 LLM_API_KEY
python -m uvicorn app.main:app --port 8000

# 2) 前端（Node 18+）
cd frontend
npm install
npm run dev                          # http://localhost:3000
```

API Key 也可在页面右上角 **⚙ 模型配置** 中填写，会写回 `backend/.env`。

## 目录结构

```
backend/
  app/
    main.py                FastAPI 入口 + CORS
    core/config.py         pydantic-settings 配置（读 .env）
    core/llm.py            OpenAI 兼容协议客户端（chat / images）
    rules/platforms.py     平台规则库 + 批量上传模板字段
    agent/
      state.py             AgentState（LangGraph 状态）
      emitter.py           进度推送器（步骤 / 日志 / 事件）
      graph.py             ★ Plan-and-Execute 状态图 + 条件边
      nodes/
        plan.py            Orchestrator 任务规划（LLM + 规则兜底）
        parse.py           商品信息解析（多模态，失败降级）
        listing.py         多语言 Listing 生成（并发 3）
        images.py          主图 + 详情页生成（并发 3，失败降级占位图）
        validate.py        规则校验 + 语言质量评分
        fix.py             不合规自动修正（最多 2 轮）
        export.py          批量上传格式生成与打包
    services/
      jobs.py              任务管理 / SSE / zip 打包
      storage.py           本地 / 阿里云 OSS
      exporters/amazon.py  openpyxl 生成 Flat File
      exporters/csv_exporter.py  pandas 生成 CSV
    api/                   meta / config / jobs 三个路由
frontend/
  app/page.tsx             4 屏容器（输入 / 进度 / 预览 / 下载）
  components/              各视图与弹窗
  lib/api.ts               后端接口封装
  lib/types.ts             TypeScript 类型
```

## Agent 工作流（LangGraph）

```
plan ──► parse ──► listing ──┬─► image ──┐
                             │           ▼
                             └────────► validate ──┬─► fix ─┐（条件回边，≤2 轮）
                                                   │        │
                                                   │◄───────┘
                                                   └─► export ──► END
```

- `after_listing`：未开启图像生成时跳过 image 节点
- `after_validate`：任一平台存在 error 级不合规且未达重试上限 → `fix`；否则 → `export`
- `recursion_limit=20` 防止死循环

访问 `http://localhost:8000/api/meta` 可拿到图的 Mermaid 描述，直接贴进方案文档。

## 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/meta` | 模型清单 / 市场 / 平台规则 / 图结构 |
| GET / PUT | `/api/config` | 读写配置（写回 `.env`） |
| POST | `/api/test` | 模型连通性测试 |
| POST | `/api/jobs` | 创建任务 |
| GET | `/api/jobs/{id}` | 任务快照 |
| GET | `/api/jobs/{id}/events` | SSE 进度流 |
| POST | `/api/jobs/{id}/cancel` | 取消任务 |
| GET | `/api/jobs/{id}/files` | 产物清单 |
| GET | `/api/jobs/{id}/asset?p=images/xx.png` | 素材图 |
| GET | `/api/jobs/{id}/download?scope=all\|amazon` | zip 下载 |

## 说明

- 平台批量上传模板为**简化演示版**（Amazon 36 列 / AliExpress 20 列 / Shopee 20 列 / TikTok 18 列），字段定义集中在 `backend/app/rules/platforms.py`，接入真实模板只需改这一个文件。
- 未启用 LangGraph checkpointer：state 中含 `emitter` / `llm` / `storage` 等运行期对象，序列化成本高；条件回边能力已完整保留。
- `demo/` 为早期 Node.js 单进程版本，保留作对照，新开发请用 `backend/` + `frontend/`。

### 图像接口说明（2026-09-01 已解决）

- qwen-image / wan 系列模型**不提供 OpenAI compatible-mode**，走 DashScope 原生接口（见阿里云官方文档《千问-图像生成与编辑 3.0 API 参考》）。此前网关 `/compatible-mode/v1/images/generations` 恒 400（`url error`）即因此所致 —— 该路径下根本没有代理图像模型。
- `LlmClient.generate_image` 默认从对话基地址推导原生端点：`{scheme}://{host}/api/v1/services/aigc/multimodal-generation/generation`。请求体为 `input.messages[].content[{text}]` 结构，size 格式 `1024*1024`（代码自动把 `x` 转 `*`），响应从 `output.choices[0].message.content[0].image` 取 URL 并立即下载落盘（该 URL 24 小时过期）。
- 实测可用模型：`qwen-image-2.0`、`wan2.7-image`、`wan2.7-image-pro`（`qwen-image-3.0` 在当前工作空间不存在）。
- 若确有支持 OpenAI Images API 的图像服务，`.env` 设 `LLM_IMAGE_BASE_URL` 即整体切换为兼容模式调用。
