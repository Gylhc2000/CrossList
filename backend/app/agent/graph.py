"""LangGraph Plan-and-Execute 工作流

    plan ──► parse ──┬─► listing ──┐
                     │             ├──► validate ──┬─► fix ──┐（条件回边，≤2 轮）
                     └─► image ────┘               │         │
                                                   │◄────────┘
                                                   └─► export ──► END

说明：
- listing 与 image 并行（二者都只依赖 knowledge_card），validate 汇聚两条分支后执行
- after_validate：任一平台存在 error 级不合规且未达重试上限 → fix（回边）；否则 → export
- validate 在修正轮只重新评分发生变化的语言，其余复用上轮结果
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.agent.nodes.export import export_node
from app.agent.nodes.fix import fix_node, pending_fix_targets
from app.agent.nodes.images import image_node
from app.agent.nodes.listing import listing_node
from app.agent.nodes.parse import parse_node
from app.agent.nodes.plan import plan_node
from app.agent.nodes.validate import validate_node
from app.agent.state import AgentState

RECURSION_LIMIT = 20


def route_after_validate(state: AgentState) -> str:
    return "fix" if pending_fix_targets(state) else "export"


def build_graph():
    g = StateGraph(AgentState)

    g.add_node("plan", plan_node)
    g.add_node("parse", parse_node)
    g.add_node("listing", listing_node)
    g.add_node("image", image_node)
    g.add_node("validate", validate_node)
    g.add_node("fix", fix_node)
    g.add_node("export", export_node)

    g.set_entry_point("plan")
    g.add_edge("plan", "parse")

    # listing 与 image 都只依赖 knowledge_card，彼此独立 —— 并行执行可省下
    # 整段图像生成时间（9 张图原本要等 Listing 全部生成完才开始）。
    # image 节点内部会自行判断是否开启图像生成。
    g.add_edge("parse", "listing")
    g.add_edge("parse", "image")
    g.add_edge("listing", "validate")   # validate 等待两条分支都完成
    g.add_edge("image", "validate")

    g.add_conditional_edges(
        "validate",
        route_after_validate,
        {"fix": "fix", "export": "export"},
    )
    g.add_edge("fix", "validate")   # 修正后回到校验，形成闭环
    g.add_edge("export", END)

    return g.compile()


# 模块级单例：图结构无状态，可复用
graph = build_graph()


async def run_agent(state: AgentState) -> dict:
    """执行 Agent，返回最终状态"""
    return await graph.ainvoke(state, config={"recursion_limit": RECURSION_LIMIT})


def graph_mermaid() -> str:
    """导出图结构（Mermaid），便于方案展示与调试"""
    try:
        return graph.get_graph().draw_mermaid()
    except Exception:
        return "graph TD\n  plan-->parse-->listing-->validate\n  validate-->|不合规|fix-->validate\n  validate-->|合规|export"
