"""Webhook/inbound trigger callbacks extracted from api/main.py (2026-11-02 relay).

The four callbacks are wired into WebhookDeliverer / InboundReceiver at module
assembly time. Their only process-level couplings — the shared background
worker pool and the pinned-version run entrypoint — are injected by the
assembler, so this module never imports the API layer back.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from atlas.iam.deps import tenant_registry
from atlas.routing import TriggerEvent

# _background_run_worker 的同形签名（位置参数前六项 + mode/run_id 关键字）；
# 装配方在 worker 定义之后注入，避免 import 期前向引用。
WorkerFn = Callable[..., None]


def build_webhook_callbacks(
    *, pool: ThreadPoolExecutor, worker: WorkerFn
) -> tuple[
    Callable[[str, str, TriggerEvent], "int | None"],
    WorkerFn,
    WorkerFn,
    Callable[[str, str, dict], None],
]:
    """装配用四回调：resolve（钉版本）/ trigger（webhook 触发）/
    inbound_trigger（入站会话触发）/ audit（投递审计）。"""

    def webhook_resolve(graph_id: str, tenant: str, event: TriggerEvent) -> int | None:
        services = tenant_registry.get(tenant)
        version, _segment = services.routing_store.resolve(graph_id, tenant=tenant, event=event)
        return version

    def webhook_trigger(
        graph_id: str, version: int, event: TriggerEvent, tenant: str, agent_id: str = ""
    ) -> None:
        services = tenant_registry.get(tenant)
        pool.submit(worker, services, tenant, graph_id, version, event, agent_id)

    def inbound_trigger(
        graph_id: str, version: int, event: TriggerEvent, tenant: str,
        agent_id: str, run_id: str | None,
    ) -> None:
        """入站会话触发（打包 BO，docs/131 D73）：承担「调度派发方」职责——submit 前
        按预置 run_id 先 begin（与调度器同款"tick 内即可见 running"语义，见
        `_background_run_worker` docstring），mode 记 `message_inbound`（U1445）。"""
        services = tenant_registry.get(tenant)
        if run_id is not None:
            services.run_store.begin(
                run_id=run_id, graph_id=graph_id, mode="message_inbound", agent_id=agent_id
            )
        pool.submit(
            worker, services, tenant, graph_id, version, event, agent_id,
            mode="message_inbound", run_id=run_id,
        )

    def webhook_audit(action: str, tenant: str, metadata: dict) -> None:
        services = tenant_registry.get(tenant)
        # AuditStore 无 metadata 列：关键事实编码进 path（不含 body/密钥）。
        path = (
            f"webhook binding={metadata.get('bindingId')} "
            f"id={metadata.get('webhookId')} shop={metadata.get('shop')}"
        )
        services.audit_store.record(
            tenant_id=tenant, actor="shopify-webhook", action=action,
            status_code=200, path=path, ip="",
        )

    return webhook_resolve, webhook_trigger, inbound_trigger, webhook_audit
