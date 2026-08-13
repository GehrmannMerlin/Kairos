"""M-09 discovery error taxonomy."""

from __future__ import annotations


class DiscoveryError(Exception):
    """M-09 discovery error base（分类到 M-03 错误分类体系）。"""


class DiscoveryValidationError(ValueError, DiscoveryError):
    """URL 或输入不通过 discovery 校验。"""
