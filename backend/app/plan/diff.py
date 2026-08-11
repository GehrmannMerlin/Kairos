"""Deterministic PlanDiff — 程序计算的结构化差异（M-08 / D-007 审计要求）。

Diff 事实由程序计算，不用 LLM 文本“计划差不多一样”。LLM 只负责用户可读摘要（reasoning）。
"""

from __future__ import annotations

from pydantic import BaseModel

from app.plan.schemas import PlanGraphDraft


class PlanDiff(BaseModel):
    added_nodes: list[str] = []
    removed_nodes: list[str] = []
    changed_parameters: dict[str, dict] = {}
    changed_dependencies: dict[str, list[str]] = {}
    changed_risk_levels: dict[str, str] = {}
    changed_resource_classes: dict[str, str] = {}
    impact_scope: str = "execution_strategy"

    @staticmethod
    def compute(before: PlanGraphDraft, after: PlanGraphDraft) -> PlanDiff:
        before_nodes = {n.node_id: n for n in before.nodes}
        after_nodes = {n.node_id: n for n in after.nodes}
        added = [nid for nid in after_nodes if nid not in before_nodes]
        removed = [nid for nid in before_nodes if nid not in after_nodes]
        changed_params: dict[str, dict] = {}
        changed_deps: dict[str, list[str]] = {}
        for nid, a in after_nodes.items():
            if nid in before_nodes:
                b = before_nodes[nid]
                if a.parameters != b.parameters:
                    changed_params[nid] = a.parameters
                if a.depends_on != b.depends_on:
                    changed_deps[nid] = a.depends_on
        # M-08 只保存 metadata；风险/资源类影响由 validator 在 Replan 时重新判定
        return PlanDiff(
            added_nodes=added,
            removed_nodes=removed,
            changed_parameters=changed_params,
            changed_dependencies=changed_deps,
            impact_scope="execution_strategy",
        )
