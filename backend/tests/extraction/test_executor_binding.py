"""install_extraction_executors 注册 + NODE_EXECUTOR 绑定（M-08 seam）。"""
from __future__ import annotations

from app.extraction.executors import install_extraction_executors
from app.plan.executors import NODE_EXECUTORS
from app.plan.nodes import NodeType


def test_install_registers_extract_and_normalize():
    install_extraction_executors()
    assert NodeType.EXTRACT in NODE_EXECUTORS
    assert NodeType.NORMALIZE in NODE_EXECUTORS
    assert callable(NODE_EXECUTORS[NodeType.EXTRACT])
    assert callable(NODE_EXECUTORS[NodeType.NORMALIZE])
