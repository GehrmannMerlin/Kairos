"""Canonical task-type vocabulary (M-06).

Single source of truth for task semantics per D-003 / D-035. The database stores
the uppercase value (e.g. ``EXPLORATORY``). Never add a second name for the same
meaning (no SEARCH / DISCOVERY / directed aliases in new code).

A fresh Draft has no known type yet; ``tasks.task_type`` is NULL until Goal
Understanding resolves it to one of the three canonical values.
"""

from __future__ import annotations

from enum import StrEnum


class TaskType(StrEnum):
    EXPLORATORY = "EXPLORATORY"
    SPECIFIED_SOURCE = "SPECIFIED_SOURCE"
    HYBRID = "HYBRID"
