"""LangGraph Agent 状态定义

Plan-and-Execute 架构：
    规划(plan) → 解析(parse) → 生成Listing(listing) → 生成图片(image)
        → 校验(validate) ──┬─ 不合规 → 修正(fix) → 回到 validate（条件回边）
                           └─ 合规/重试耗尽 → 打包(export) → END
"""
from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    """Agent 全局状态（单次任务内共享）"""

    # ---- 运行环境 ----
    job_id: str
    params: dict[str, Any]        # 用户输入参数
    emitter: Any                  # 进度推送器
    llm: Any                      # LlmClient
    storage: Any                  # Storage

    # ---- 规划 ----
    plan: dict[str, Any]          # 任务计划：平台→语言分配、图片清单、并发策略

    # ---- 产物 ----
    knowledge_card: dict[str, Any]
    listings: dict[str, dict[str, Any]]     # unit_key("pk:mk") -> listing
    images: list[dict[str, Any]]
    platforms: list[dict[str, Any]]         # 校验后的任务单元结果（含 pk/market/lang）

    # ---- 校验 ----
    issues: dict[str, list[dict]]           # unit_key -> [Issue dict]
    quality: dict[str, dict[str, Any]]      # unit_key -> {score, comments, ...}
    retry: dict[str, int]                   # unit_key -> 已修正轮数
    fixed_langs: list[str]                  # 本轮被修正的 unit_key，供 validate 按需重评

    # ---- 交付 ----
    artifacts: dict[str, list[dict]]        # platform_key -> [{name,size,type}]
    report: dict[str, Any]

    # ---- 运行信息 ----
    warnings: list[str]
    logs: list[dict[str, Any]]
    error: str | None


def new_state(
    job_id: str,
    params: dict[str, Any],
    emitter: Any,
    llm: Any,
    storage: Any,
) -> AgentState:
    return AgentState(
        job_id=job_id,
        params=params,
        emitter=emitter,
        llm=llm,
        storage=storage,
        plan={},
        knowledge_card={},
        listings={},
        images=[],
        platforms=[],
        issues={},
        quality={},
        retry={},
        fixed_langs=[],
        artifacts={},
        report={},
        warnings=[],
        logs=[],
        error=None,
    )
