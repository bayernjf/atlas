"""MCP server 面 v1（Atlas 的第三个对外出入口；docs/91、ADR T33）。

形状＝①官方 `mcp` Python SDK v2（协议形状由 SDK 承担，Atlas 不自造）＋②stdio 传输
＋③只读/plan-only 工具面。前两个出入口是平台 REST（docs/80）与 A2A 执行 Agent 面
（docs/90，ADR T32）。

- server.py：租户绑定（fail-closed）＋只读投影装配。
- tools.py：7 个只读工具的定义与实现。
- __main__.py：`python -m atlas.mcp` stdio 入口。

**不 import atlas.api.main**：本进程不拉起 Web 栈、调度线程与恢复扫描。
"""

from atlas.mcp.server import (
    SERVER_INSTRUCTIONS,
    SERVER_NAME,
    TENANT_ENV,
    TenantBindingError,
    TenantViews,
    build_server,
    resolve_tenant_id,
)
from atlas.mcp.tools import TOOL_NAMES, register_tools

__all__ = [
    "SERVER_INSTRUCTIONS",
    "SERVER_NAME",
    "TENANT_ENV",
    "TOOL_NAMES",
    "TenantBindingError",
    "TenantViews",
    "build_server",
    "register_tools",
    "resolve_tenant_id",
]
