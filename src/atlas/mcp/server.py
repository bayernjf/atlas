"""MCP server 面装配与租户绑定（docs/91 §4；ADR T33）。

与 REST 面的分工：本进程**不 import atlas.api.main**——否则会连带拉起 FastAPI app、
调度线程与启动恢复扫描，把一个只读工具进程变成第二个后台服务（也违背单副本纪律）。
数据访问只经 `iam.registry.TenantRegistry` 与既有只读投影函数。
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from atlas.harness.runtime import build_base_registry, build_runtime_registry, resolve_database_client
from atlas.iam.principals import SEED_TENANTS
from atlas.iam.registry import STORAGE_BACKEND, TenantRegistry, TenantServices
from atlas.security.bootstrap import demo_surface_enabled
from atlas.storage.recovery import interruption_view
from atlas.template import get_template, list_templates
from atlas.web.i18n import localize_template, localize_tool_desc, resolve_locale
from mcp.server import MCPServer

from .tools import ReadOnlyViews, register_tools

#: 进程级租户绑定（fail-closed）：缺失或未知即拒启，不猜测、不落默认租户。
TENANT_ENV = "ATLAS_MCP_TENANT_ID"

SERVER_NAME = "atlas"
SERVER_INSTRUCTIONS = (
    "Atlas MCP 面 v1：**只读**。可列图/模板/运行/挂起帧/能力，可读取单张图与单个模板；"
    "不起运行、不改图、不调 LLM、不裁决人工审批。"
)

# 本进程内的 TenantRegistry 单例（与 api 侧同形，各自进程一份；测试可注入）。
_REGISTRY = TenantRegistry()

# MCP 面没有 Accept-Language：按 REST 的缺省档位本地化，两侧默认形状一致。
_DEFAULT_LOCALE = resolve_locale(None)


class TenantBindingError(RuntimeError):
    """租户绑定失败：拒绝启动（docs/91 §4 的 fail-closed）。"""


def resolve_tenant_id(env: Mapping[str, str] | None = None) -> str:
    """读 `ATLAS_MCP_TENANT_ID`，必须是已知租户；否则抛 `TenantBindingError`。"""
    source = os.environ if env is None else env
    raw = (source.get(TENANT_ENV) or "").strip()
    if not raw:
        raise TenantBindingError(
            f"缺少环境变量 {TENANT_ENV}：MCP server 必须显式绑定一个租户，拒绝启动"
        )
    if raw not in SEED_TENANTS:
        known = "、".join(sorted(SEED_TENANTS))
        raise TenantBindingError(
            f"{TENANT_ENV}={raw!r} 不是已知租户（已知：{known}）：拒绝启动，"
            "不猜测、不落到默认租户"
        )
    return raw


class TenantViews:
    """绑定租户的只读投影集合（`ReadOnlyViews` 的唯一实现）。

    每个方法只走 `store.list()/store.get()` 与既有纯投影函数——**没有写路径**
    （docs/91 §3 铁律 1）。列表类工具统一带 `backend` 自报（§7 第 2 项＝按推荐带上）：
    内存档下本进程是独立空实例，"空列表"不等于"平台上没有数据"。
    """

    def __init__(
        self,
        services: TenantServices,
        tenant_id: str,
        *,
        backend: str | None = None,
    ) -> None:
        self._services = services
        self._tenant_id = tenant_id
        self._backend = backend or STORAGE_BACKEND
        self._registry: Any = None

    # --- 图 ---
    def list_graphs(self) -> dict[str, Any]:
        return {"backend": self._backend, "items": self._services.graph_store.list()}

    def get_graph(self, graph_id: str) -> dict[str, Any] | None:
        return self._services.graph_store.get(graph_id)

    # --- 模板 ---
    def list_templates(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = [
            localize_template(
                {
                    "id": template.id,
                    "name": template.name,
                    "description": template.description,
                    "tags": template.tags,
                    "node_count": len(template.graph["nodes"]),
                    "source": "catalog",
                    "deletable": False,
                },
                _DEFAULT_LOCALE,
            )
            for template in list_templates()
        ]
        items.extend(
            {
                "id": template.id,
                "name": template.name,
                "description": template.description,
                "tags": template.tags,
                "node_count": len(template.graph.get("nodes", [])),
                "source": "user",
                "deletable": True,
                "created_at": template.created_at,
            }
            for template in self._services.user_templates.list()
        )
        return {"backend": self._backend, "items": items}

    def get_template(self, template_id: str) -> dict[str, Any] | None:
        template = get_template(template_id)
        if template is not None:
            return localize_template(
                {**template.model_dump(), "source": "catalog", "deletable": False},
                _DEFAULT_LOCALE,
            )
        user_template = self._services.user_templates.get(template_id)
        if user_template is None:
            return None
        return {**user_template.model_dump(), "source": "user", "deletable": True}

    # --- 运行 / 挂起帧 ---
    def list_runs(self, status: str | None, limit: int) -> dict[str, Any]:
        return {
            "backend": self._backend,
            "items": self._services.run_store.list(status=status, limit=limit),
        }

    def list_interruptions(self) -> dict[str, Any]:
        run_store = self._services.run_store
        return interruption_view(
            backend=self._backend,
            tenant_id=self._tenant_id,
            engine=self._engine(),
            run_status_of=lambda run_id: (run_store.get(run_id) or {}).get("status"),
            include_resume_token=False,
        )

    # --- 能力 ---
    def list_adapters(self) -> dict[str, Any]:
        items = [
            {
                **adapter,
                "tools": [localize_tool_desc(tool, _DEFAULT_LOCALE) for tool in adapter.get("tools", [])],
            }
            for adapter in self._runtime_registry().list_adapters()
        ]
        return {"backend": self._backend, "items": items}

    # --- 内部 ---
    def _engine(self) -> Any:
        if self._backend != "pg":
            return None
        from atlas.storage.pg import get_pg_backend

        return get_pg_backend().engine

    def _runtime_registry(self) -> Any:
        """执行期适配器注册表：与 `GET /api/adapters` 同一份装配（harness.runtime）。"""
        if self._registry is None:
            # 只读面不持有任何凭证：secret_provider=None 只影响执行，不影响发现投影。
            self._registry = build_runtime_registry(
                self._services,
                build_base_registry(demo_surface_enabled(), resolve_database_client(demo_surface_enabled())),
                None,
            )
        return self._registry


def build_server(
    tenant_id: str,
    *,
    registry: TenantRegistry | None = None,
    backend: str | None = None,
) -> MCPServer:
    """按绑定租户装配 MCP server（工具集见 tools.py）。"""
    services = (registry or _REGISTRY).get(tenant_id)
    views: ReadOnlyViews = TenantViews(services, tenant_id, backend=backend)
    server = MCPServer(name=SERVER_NAME, instructions=SERVER_INSTRUCTIONS)
    register_tools(server, views)
    return server
