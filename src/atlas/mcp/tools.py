"""MCP 工具面 v1（只读／plan-only；docs/91 §3）。

工具集锚定既有只读 REST 投影，不发明新读面；每个工具只回 **text content**（JSON
序列化串），v1 不出 `outputSchema`／`structuredContent`（§7 第 3 项＝按推荐不出的）。

铁律（§3）：
1. 只调只读投影，不起 run、不改图、不调 LLM、不裁决审批；
2. 不带 `tenant_id` 入参（租户在进程启动时绑定，见 server.py）；
3. 命名 `atlas_<verb>_<noun>` snake_case、不带斜杠（docs/77 R8 教训）。

失败口径：业务失败（未知 id／非法枚举／越界 limit）一律**工具级错误**
（`isError: true`），不在协议层拒标准调用方（同 docs/90 §4 纪律）。
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

#: `GET /api/runs` 的 status 枚举（docs/24 §4）；非法值在工具内判，不进协议层。
RUN_STATUSES: tuple[str, ...] = (
    "running", "suspended", "completed", "failed", "interrupted", "cancelled",
)
DEFAULT_RUN_LIMIT = 50
MAX_RUN_LIMIT = 200

#: 工具名（v1 冻结的 7 项；docs/91 §7 第 1 项＝按推荐锁这 7 项）。
TOOL_NAMES: tuple[str, ...] = (
    "atlas_list_graphs",
    "atlas_get_graph",
    "atlas_list_templates",
    "atlas_get_template",
    "atlas_list_runs",
    "atlas_list_interruptions",
    "atlas_list_adapters",
)


class ReadOnlyViews(Protocol):
    """工具面唯一的数据入口：只读投影集合（由 server.py 按绑定租户装配）。"""

    def list_graphs(self) -> dict[str, Any]: ...
    def get_graph(self, graph_id: str) -> dict[str, Any] | None: ...
    def list_templates(self) -> dict[str, Any]: ...
    def get_template(self, template_id: str) -> dict[str, Any] | None: ...
    def list_runs(self, status: str | None, limit: int) -> dict[str, Any]: ...
    def list_interruptions(self) -> dict[str, Any]: ...
    def list_adapters(self) -> dict[str, Any]: ...


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def register_tools(server: MCPServer, views: ReadOnlyViews) -> None:
    """把 7 个只读工具注册到 server（v1 冻结集）。"""

    @server.tool(
        structured_output=False,
        description="列出已保存的图（不含图体，与 GET /api/graphs 同投影）",
    )
    def atlas_list_graphs() -> str:
        return _json(views.list_graphs())

    @server.tool(
        structured_output=False,
        description="按 id 取一张已保存图的原文（与 GET /api/graphs/{id} 同投影）",
    )
    def atlas_get_graph(graph_id: str) -> str:
        raw = views.get_graph(graph_id)
        if raw is None:
            # 跨租户与不存在同形：不泄漏资源存在性（04 §5.14）
            raise ToolError(f"Graph 不存在：{graph_id}")
        return _json(raw)

    @server.tool(
        structured_output=False,
        description="列出流程模板（内置目录在前、本租户自建在后；列表投影不含 graph）",
    )
    def atlas_list_templates() -> str:
        return _json(views.list_templates())

    @server.tool(
        structured_output=False,
        description="按 id 取模板完整元数据（含 graph）",
    )
    def atlas_get_template(template_id: str) -> str:
        raw = views.get_template(template_id)
        if raw is None:
            raise ToolError(f"模板不存在：{template_id}")
        return _json(raw)

    @server.tool(
        structured_output=False,
        description="列出本租户的运行（新→旧；status 过滤可选，limit 1-200 缺省 50）",
    )
    def atlas_list_runs(status: str | None = None, limit: int = DEFAULT_RUN_LIMIT) -> str:
        if status is not None and status not in RUN_STATUSES:
            raise ToolError(f"非法的 status 过滤值：{status}（可选：{'、'.join(RUN_STATUSES)}）")
        if not 1 <= limit <= MAX_RUN_LIMIT:
            raise ToolError(f"limit 必须在 1 到 {MAX_RUN_LIMIT} 之间：{limit}")
        return _json(views.list_runs(status, limit))

    @server.tool(
        structured_output=False,
        description=(
            "列出本租户挂起帧的只读投影（只描述不处理；对外面不含 resumeToken，"
            "内存档自报 visibility=frames-not-persisted）"
        ),
    )
    def atlas_list_interruptions() -> str:
        return _json(views.list_interruptions())

    @server.tool(
        structured_output=False,
        description="列出本租户可用的能力与工具声明（与 GET /api/adapters 同表，只读）",
    )
    def atlas_list_adapters() -> str:
        return _json(views.list_adapters())
