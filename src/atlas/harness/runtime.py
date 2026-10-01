"""执行期适配器注册表装配（04 §5.14）。

原为 `api/main.py` 内的私有函数（`_build_demo_registry`／`_runtime_registry`）。抽到此处
是 docs/91 §3 的直接后果：`atlas_list_adapters` 锚 `GET /api/adapters` 的投影，若 MCP 侧
另抄一份装配，两个只读面迟早看到不同的适配器表。故**只留一份**，REST 与 MCP 共用。
"""

from __future__ import annotations

from typing import Any

from atlas.channels.adapter import ShopifyHarnessAdapter
from atlas.database.adapter import DatabaseHarnessAdapter
from atlas.database.service import DatabaseClient, demo_engine
from atlas.harness.base import Permission
from atlas.harness.registry import AdapterRegistry
from atlas.httpapi.adapter import HttpApiHarnessAdapter
from atlas.memory.adapter import MemoryHarnessAdapter
from atlas.message.adapter import MessageHarnessAdapter
from atlas.openapi.adapter import ImportedApiHarnessAdapter
from atlas.shop.adapter import ShopHarnessAdapter
from atlas.shop.service import DemoShopService

# 适配器在平台内一律按全权限装配：权限收窄发生在人工审批与出向准入，不在这里
# （04 §4.4；docs/32 §5）。
FULL_PERMISSIONS = {Permission.READ, Permission.WRITE, Permission.DELETE, Permission.FINANCIAL}


def resolve_database_client(demo_surface: bool) -> DatabaseClient | None:
    """数据适配器出站连接（04 §4.7）：ATLAS_DATABASE_URL 出站连接（与平台 DATABASE_URL 隔离）。

    未配置时**仅演示面**回退内置 SQLite demo 订单库；docs/77 R2：prod 且未开 demo 面
    返回 None（该适配器不注册，图里选不到），而不是静默打到演示 fixture。
    """
    client = DatabaseClient.from_env()
    if client is None and demo_surface:
        client = DatabaseClient(demo_engine(), demo=True)
    return client


def build_base_registry(
    demo_surface: bool,
    db_client: DatabaseClient | None,
    *,
    shop: DemoShopService | None = None,
) -> AdapterRegistry:
    """全局基础设施/演示适配器（04 §5.14）。

    docs/77 R2：`shop`（进程内 `DemoShopService`）与 `database`（内置 SQLite demo）
    属**演示面**——prod 且未开 demo 面即不注册（与 HTTP mock 路由共用
    `ATLAS_ENABLE_DEMO_MOCK` 一处判定）。`http`/`message`/`memory` 不依赖演示 fixture，照常注册。
    """
    from atlas.httpapi.service import HttpApiClient

    registry = AdapterRegistry()
    if demo_surface:
        registry.register(
            ShopHarnessAdapter(
                service=shop or DemoShopService(),
                granted_permissions=FULL_PERMISSIONS,
            )
        )
    registry.register(
        HttpApiHarnessAdapter(
            client=HttpApiClient.from_env(),
            granted_permissions=FULL_PERMISSIONS,
        )
    )
    if db_client is not None:
        registry.register(
            DatabaseHarnessAdapter(
                client=db_client,
                granted_permissions=FULL_PERMISSIONS,
            )
        )
    # 全局注册表里的 message 实例仅供适配器发现；执行期注册表替换为租户消息服务
    registry.register(MessageHarnessAdapter(granted_permissions=FULL_PERMISSIONS))
    # 全局注册表里的 memory 实例仅供适配器发现；执行期注册表替换为租户记忆存储（docs/26 §5.1）
    registry.register(MemoryHarnessAdapter(granted_permissions=FULL_PERMISSIONS))
    return registry


def build_runtime_registry(
    services: Any,
    base: AdapterRegistry,
    secret_provider: Any,
) -> AdapterRegistry:
    """执行期注册表：全局基础设施之上合并本租户渠道适配器（docs/38 §1E）。

    message／memory 两个适配器替换为当前租户实例（04 §5.14 分区；06 §6.12）。
    """
    registry = AdapterRegistry()
    for item in base.list_adapters():
        adapter = base.get(item["id"])
        if item["id"] == "message":
            adapter = MessageHarnessAdapter(
                service=services.message_service, granted_permissions=FULL_PERMISSIONS
            )
        if item["id"] == "memory":
            adapter = MemoryHarnessAdapter(
                repo=services.memory_store, granted_permissions=FULL_PERMISSIONS
            )
        registry.register(adapter)
    for view in services.channel_registry.list():
        registry.register(
            ShopifyHarnessAdapter(
                view["id"], services.channel_registry,
                granted_permissions=FULL_PERMISSIONS,
            )
        )
    for imported in services.openapi_imports.list():
        registry.register(
            ImportedApiHarnessAdapter(
                imported,
                secret_provider=secret_provider,
                granted_permissions=FULL_PERMISSIONS,
            )
        )
    return registry
