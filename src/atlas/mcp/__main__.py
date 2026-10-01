"""`python -m atlas.mcp`：MCP server 的 stdio 入口（docs/91 §4）。

**stdout 纯净性（硬约束）**：stdio 传输下 stdout 只能含 JSON-RPC 帧，故日志一律走
stderr——这是 stdio 形态最易踩的坑（任何 print／stdout handler 都会污染协议流）。
租户绑定 fail-closed：缺失或未知租户即非零退出（不猜测、不落默认租户）。
"""

from __future__ import annotations

import logging
import sys

from atlas.mcp.server import TENANT_ENV, TenantBindingError, build_server, resolve_tenant_id

logger = logging.getLogger("atlas.mcp")


def main() -> int:
    # MCPServer 构造时会调 SDK 的 configure_logging（root basicConfig）；先占住 root
    # 并指向 stderr，SDK 那次 basicConfig 即成为 no-op，stdout 始终干净。
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    try:
        tenant_id = resolve_tenant_id()
    except TenantBindingError as exc:
        logger.error("MCP server 拒绝启动：%s", exc)
        return 2
    logger.info("MCP server 启动：transport=stdio tenant=%s", tenant_id)
    build_server(tenant_id).run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
