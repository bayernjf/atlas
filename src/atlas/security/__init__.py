"""真实接入安全准入（docs/32，P0 批 3）。

运营体出向调用真实外部系统前的纯逻辑安全层：SSRF egress 校验、
凭证信封与运行时秘密注入。重试/熔断在 httpapi.resilience，DB 只读
审查在 database.guard。
"""
