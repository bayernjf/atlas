"""假物流适配器（M7，docs/20 §4.2 沙盘）。

进程内模拟「物流签收入库」，与 demo shop 同构、不接真实系统——供退货退款沙盘
链路「退货退款必须签收后放款」的跨 Bot 时序依赖验证。沙盘语义期不解除 D31。
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

_CHECK_RECEIPT_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string"}},
    "required": ["order_id"],
}

_RECEIPT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "order_id": {"type": "string"},
        "signed": {"type": "boolean"},
    },
    "required": ["order_id", "signed"],
}


class LogisticsAdapter(HarnessAdapter):
    adapter_id = "logistics"
    adapter_type = "logistics"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.receipts: dict[str, bool] = {}  # order_id -> signed

    def list_capabilities(self) -> list[Capability]:
        return [
            Capability(
                name="check_receipt",
                description="查询退货是否已签收入库",
                action="check_receipt",
                input_schema=_CHECK_RECEIPT_INPUT_SCHEMA,
                output_schema=_RECEIPT_OUTPUT_SCHEMA,
                permission=Permission.READ,
                is_idempotent=True,
            ),
            Capability(
                name="sign_receipt",
                description="标记退货已签收入库",
                action="sign_receipt",
                input_schema=_CHECK_RECEIPT_INPUT_SCHEMA,
                output_schema=_RECEIPT_OUTPUT_SCHEMA,
                permission=Permission.WRITE,
            ),
        ]

    def _execute(self, request: ActionRequest) -> ActionResult:
        order_id = request.parameters.get("order_id", "")
        if not order_id:
            return ActionResult.failed(StructuredError("MISSING_PARAMETER", "缺少 order_id"))
        if request.capability_name == "check_receipt":
            return ActionResult.success({"order_id": order_id, "signed": self.receipts.get(order_id, False)})
        if request.capability_name == "sign_receipt":
            self.receipts[order_id] = True
            return ActionResult.success({"order_id": order_id, "signed": True})
        return ActionResult.failed(StructuredError("UNKNOWN_CAPABILITY", request.capability_name))

    def observe(self) -> Observation:
        return Observation(
            url="demo://logistics/admin/receipts",
            title="假物流签收控制台",
            data={"receipts": self.receipts},
        )
