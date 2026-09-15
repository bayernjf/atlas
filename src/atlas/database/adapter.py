"""通用 SQL 数据 Harness 适配器（adapter_id="database", adapter_type="database"）。

双能力 database/query（read，幂等，结果集摘要）与 database/execute
（write，非幂等，rowcount）；参数契约见 04 §4.7 权威 blockquote，
运行时语义见 06 §6.7。
"""

from __future__ import annotations

from atlas.harness.base import (
    ActionRequest,
    ActionResult,
    Capability,
    HarnessAdapter,
    Observation,
    Permission,
    StructuredError,
)
from .service import DEFAULT_LIMIT, MAX_LIMIT, DatabaseAdapterError, DatabaseClient

_QUERY_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"type": "string", "description": "SELECT 查询语句；参数值走绑定 params，不要拼接"},
        "params": {"description": "命名绑定参数对象（或对象数组），防 SQL 注入的推荐路径"},
        "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": DEFAULT_LIMIT},
    },
    "required": ["sql"],
}

_QUERY_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "columns": {"type": "array", "items": {"type": "string"}},
        "rows": {"type": "array", "items": {"type": "object"}},
        "row_count": {"type": "integer"},
        "truncated": {"type": "boolean"},
    },
    "required": ["columns", "rows", "row_count", "truncated"],
}

_EXECUTE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"type": "string", "description": "INSERT/UPDATE/DELETE/DDL 语句；参数值走绑定 params"},
        "params": {"description": "命名绑定参数对象（或对象数组）"},
    },
    "required": ["sql"],
}

_EXECUTE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"rowcount": {"type": "integer"}},
    "required": ["rowcount"],
}


class DatabaseHarnessAdapter(HarnessAdapter):
    adapter_id = "database"
    adapter_type = "database"

    def __init__(self, client: DatabaseClient | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.client = client  # 未注入且 ATLAS_DATABASE_URL 缺失时 fail-closed；demo 由宿主显式注入

    def list_capabilities(self) -> list[Capability]:
        return [
            Capability(
                name="query",
                description="通用 SQL 只读查询，返回结果集摘要（超 limit 截断）",
                action="database_query",
                input_schema=_QUERY_INPUT_SCHEMA,
                output_schema=_QUERY_OUTPUT_SCHEMA,
                permission=Permission.READ,
                timeout=30.0,
                is_idempotent=True,
            ),
            Capability(
                name="execute",
                description="通用 SQL 写操作（INSERT/UPDATE/DELETE/DDL），返回影响行数",
                action="database_execute",
                input_schema=_EXECUTE_INPUT_SCHEMA,
                output_schema=_EXECUTE_OUTPUT_SCHEMA,
                permission=Permission.WRITE,
                timeout=30.0,
                is_idempotent=False,
            ),
        ]

    def _get_client(self) -> DatabaseClient:
        if self.client is None:
            self.client = DatabaseClient.from_env()
        if self.client is None:
            raise DatabaseAdapterError(
                "DB_NOT_CONFIGURED", "未配置 ATLAS_DATABASE_URL 且未注入数据库连接"
            )
        return self.client

    def _execute(self, request: ActionRequest) -> ActionResult:
        params = request.parameters
        try:
            client = self._get_client()
            if request.capability_name == "query":
                output = client.query(
                    sql=params.get("sql"),
                    params=params.get("params"),
                    limit=params.get("limit", DEFAULT_LIMIT),
                )
            elif request.capability_name == "execute":
                output = client.execute(
                    sql=params.get("sql"),
                    params=params.get("params"),
                )
            else:
                return ActionResult.failed(
                    StructuredError("UNKNOWN_CAPABILITY", f"未知能力：{request.capability_name}")
                )
        except DatabaseAdapterError as exc:
            return ActionResult.failed(StructuredError(exc.code, str(exc)))
        return ActionResult.success(output)

    def observe(self) -> Observation:
        try:
            client = self._get_client()
        except DatabaseAdapterError:
            return Observation(
                url="obs://database?unconfigured",
                title="数据适配器（通用 SQL）",
                data={"url": "", "demo": False, "last_operation": None},
            )
        return Observation(
            url=f"obs://database?{client.masked_url}",
            title="数据适配器（通用 SQL）",
            data={
                "url": client.masked_url,
                "demo": client.demo,
                "last_operation": client.last_operation,
            },
        )
