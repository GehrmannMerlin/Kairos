from __future__ import annotations

import pytest
from app.db import close_engine


@pytest.fixture(autouse=True)
async def dispose_database_engine_after_each_test():
    yield
    await close_engine()
