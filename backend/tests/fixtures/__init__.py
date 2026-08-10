"""测试专用 fixtures：仅在测试 worker（fixture_worker / test worker）注册。

绝不允许任何 fixture 执行单元进入 app.worker 的 Production Worker（I-002 / M-07 边界）。
"""
