"""M-10 crawling module: 网页获取阶梯、Scrapy 批量、Playwright、凭据与 PageSnapshot。

边界（D-009 / D-070 / D-017）：
- 只消费 M-09 READY_FOR_FETCH + AccessDecision=ALLOW 的 URL。
- Scrapy 只是静态层批量执行模式，不是权限升级工具。
- Playwright 只在有 EscalationEvidence（或已验证 SiteFetchStrategy）时运行。
- 原始正文只进 ObjectStorage；DB 只存 metadata/hash/ref。
- Secret 只在 Activity 执行时临时解密，绝不进入日志/Temporal/事件/快照 metadata。
"""
