"""Demo 电商平台 Harness 适配器（channel 包，遵循 09 待定项 2 决策）。

进程内实现 HarnessAdapter 同构契约（06 §6.4），能力：
- shop/login（write）
- shop/list_pending_refunds（read）
- shop/execute_refund（financial；高风险，需授权）
- shop/request_human_approval（write）

工具命名 `<adapter_id>/<capability>`，与前端 tool_call 配置一致。
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
from .service import DemoShopService

_REFUND_ORDER_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "order_id": {"type": "string"},
        "reason": {"type": "string"},
        "amount": {"type": "number"},
    },
    "required": ["order_id", "reason", "amount"],
}

_LOGIN_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "username": {"type": "string"},
        "password": {"type": "string"},
    },
    "required": ["username", "password"],
}

_LOGIN_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"logged_in": {"type": "boolean"}},
    "required": ["logged_in"],
}

_LIST_PENDING_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"orders": {"type": "array", "items": _REFUND_ORDER_ITEM_SCHEMA}},
    "required": ["orders"],
}

_ORDER_REF_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "order_id": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": ["order_id"],
}

_REFUND_RESULT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "order_id": {"type": "string"},
        "status": {"type": "string", "enum": ["refunded", "human_review"]},
    },
    "required": ["order_id", "status"],
}

_PROCESS_REFUND_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "order_id": {"type": "string"},
        "action": {"type": "string", "enum": ["approve_refund", "request_human_approval"]},
        "note": {"type": "string"},
    },
    "required": ["order_id", "action"],
}


class ShopHarnessAdapter(HarnessAdapter):
    adapter_id = "shop"
    adapter_type = "shop"

    def __init__(self, service: DemoShopService | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.service = service or DemoShopService()

    def list_capabilities(self) -> list[Capability]:
        return [
            Capability(
                name="login",
                description="登录商家售后控制台",
                action="login",
                input_schema=_LOGIN_INPUT_SCHEMA,
                output_schema=_LOGIN_OUTPUT_SCHEMA,
                permission=Permission.WRITE,
            ),
            Capability(
                name="list_pending_refunds",
                description="获取待处理退款单列表",
                action="list_pending_refunds",
                output_schema=_LIST_PENDING_OUTPUT_SCHEMA,
                permission=Permission.READ,
                is_idempotent=True,
            ),
            Capability(
                name="execute_refund",
                description="对指定订单执行退款（资金操作）",
                action="execute_refund",
                input_schema=_ORDER_REF_INPUT_SCHEMA,
                output_schema=_REFUND_RESULT_OUTPUT_SCHEMA,
                permission=Permission.FINANCIAL,
            ),
            Capability(
                name="request_human_approval",
                description="对指定订单发起人工审批",
                action="request_human_approval",
                input_schema=_ORDER_REF_INPUT_SCHEMA,
                output_schema=_REFUND_RESULT_OUTPUT_SCHEMA,
                permission=Permission.WRITE,
            ),
            Capability(
                name="process_refund",
                description="按上游 AI 决策动作执行退款或转人工审批",
                action="process_refund",
                input_schema=_PROCESS_REFUND_INPUT_SCHEMA,
                output_schema=_REFUND_RESULT_OUTPUT_SCHEMA,
                permission=Permission.FINANCIAL,
            ),
        ]

    def _execute(self, request: ActionRequest) -> ActionResult:
        params = request.parameters
        if request.capability_name == "login":
            ok = self.service.login(params.get("username", ""), params.get("password", ""))
            if not ok:
                return ActionResult.failed(StructuredError("AUTH_FAILED", "登录失败：用户名或密码错误"))
            return ActionResult.success({"logged_in": True})

        if request.capability_name == "list_pending_refunds":
            return ActionResult.success({"orders": self.service.list_pending_refunds()})

        order_id = params.get("order_id")
        if not order_id:
            return ActionResult.failed(StructuredError("MISSING_PARAMETER", "缺少 order_id"))
        note = params.get("note", "")
        try:
            if request.capability_name == "execute_refund":
                return ActionResult.success(self.service.execute_refund(order_id, note))
            if request.capability_name == "request_human_approval":
                return ActionResult.success(self.service.request_human_approval(order_id, note))
            if request.capability_name == "process_refund":
                action = params.get("action")
                if action == "approve_refund":
                    return ActionResult.success(self.service.execute_refund(order_id, note))
                if action == "request_human_approval":
                    return ActionResult.success(self.service.request_human_approval(order_id, note))
                return ActionResult.failed(StructuredError("INVALID_ACTION", f"未知退款动作：{action}"))
        except KeyError as exc:
            return ActionResult.failed(StructuredError("ORDER_NOT_FOUND", str(exc)))
        return ActionResult.failed(StructuredError("UNKNOWN_CAPABILITY", request.capability_name))

    def observe(self) -> Observation:
        return Observation(
            url="demo://shop/admin/refunds",
            title="Demo 商家售后控制台",
            data={"logged_in": self.service.logged_in},
        )
