"""按租户装配的进程内服务注册表（04 §5.14）。

每租户独立持有图/录制/反馈/消息/审批/调试/监控一套实例，id 计数各自从 1 起；
模板目录、适配器注册表、demo 店铺/mock 与出向连接为全局基础设施，不在此分区。
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

from atlas.channels.registry import build_channel_registry
from atlas.channels.deliveries import InMemoryDeliveryStore
from atlas.channels.pg_deliveries import PgDeliveryStore
from atlas.connections.service import build_connection_service
from atlas.connections.store import ConnectionStore
from atlas.collaboration.cancellations import RunCancellationBroker
from atlas.collaboration.event_waits import EventWaitBroker
from atlas.coordination import TaskStore
from atlas.observability.audit import AuditRepository, AuditStore
from atlas.message.service import MessageService
from atlas.message.smtp import get_smtp_sender
from atlas.message.im import get_im_sender
from atlas.message.webhook import get_webhook_sender
from atlas.openapi.pg_store import PgImportStore
from atlas.openapi.store import ImportStore
from atlas.recording import ReportStore, ShadowStore
from atlas.recording.pg_reports import PgReportStore
from atlas.routing import RoutingStore
from atlas.storage.base import (
    ApprovalRepository,
    DebugRepository,
    FeedbackRepository,
    GraphRepository,
    MemoryRepository,
    MonitoringRepository,
    RecordingRepository,
    RunRepository,
)
from atlas.storage.memory import (
    ApprovalBroker,
    DebuggerBroker,
    FeedbackStore,
    GraphStore,
    MemoryStore,
    MonitoringStore,
    RecordingStore,
    RunStore,
)


# M5b 后端切换（docs/24 §1.2②）：ATLAS_STORAGE_BACKEND=pg 装配 PG 实现，
# 缺省 memory（进程内实现为默认/测试后端）。SessionStore 是全局单例（iam/deps.py），
# 不在 TenantServices，其后端切换不在此处。
STORAGE_BACKEND = os.environ.get("ATLAS_STORAGE_BACKEND", "memory")


@dataclass
class TenantServices:
    graph_store: GraphRepository
    recording_store: RecordingRepository
    feedback_store: FeedbackRepository
    message_service: object  # MessageService 是服务非存储，不进 Repository 抽象（docs/24 §1.1）
    approval_broker: ApprovalRepository
    debug_broker: DebugRepository
    cancellation_broker: RunCancellationBroker  # B 包协作式急停（进程内，memory/PG 档均内存实例）
    event_wait_broker: EventWaitBroker  # wait 节点事件等待（进程内 v1，docs/47；两档均内存实例）
    monitoring: MonitoringRepository
    run_store: RunRepository
    task_store: TaskStore
    routing_store: RoutingStore
    report_store: ReportStore | PgReportStore  # docs/56：批量回放报告（内存 ring / PG release_reports）
    shadow_store: ShadowStore  # D26 影子模式：旁路运行记录 ring（进程内，两档均挂内存实例，docs/33 §3）
    memory_store: MemoryRepository  # M11 长期记忆 fact/preference（批 3 PG 档换 PgMemoryStore）
    audit_store: AuditRepository  # T6 写操作审计（docs/35 §6；ring/PG 两档，reset 不清）
    connection_service: object  # T4 OAuth2 连接（docs/35 §4；业务服务，内存/PG 两档 store，reset 不清）
    channel_registry: object  # 真实渠道绑定（docs/38；ADR T28，reset 不清）
    webhook_deliveries: object  # 入站投递去重/死信（docs/40；内存/PG 两档，reset 不清）
    openapi_imports: ImportStore | PgImportStore  # OpenAPI 导入规格（docs/42/43；内存/PG 两档，reset 不清）


class TenantRegistry:
    def __init__(self) -> None:
        self._tenants: dict[str, TenantServices] = {}
        self._lock = threading.Lock()

    def get(self, tenant_id: str) -> TenantServices:
        with self._lock:
            services = self._tenants.get(tenant_id)
            if services is None:
                services = self._create_services(tenant_id)
                self._tenants[tenant_id] = services
            return services

    def peek(self, tenant_id: str) -> TenantServices | None:
        """取已装配租户；不存在返回 None，绝不惰性创建（邮件公开路径用）。"""
        with self._lock:
            return self._tenants.get(tenant_id)

    def all_tenant_ids(self) -> list[str]:
        """已装配（曾被访问过）的租户 id 快照；不触发惰性创建（/metrics 用）。"""
        with self._lock:
            return list(self._tenants.keys())

    @staticmethod
    def _create_services(tenant_id: str) -> TenantServices:
        # M5a：八个进程内 store 统一自 storage.memory 构造（GraphStore/FeedbackStore
        # 已自 api/main.py 搬出，延迟导入环随之消除，顶层导入安全）。
        # M5b：ATLAS_STORAGE_BACKEND=pg 时，租户 store 换 PG 实现（per-tenant 绑定）；
        # debug 会话是短命临时态、其帧落库不在 U43–U45 验收，批 1 保持内存实现。
        if STORAGE_BACKEND == "pg":
            from atlas.storage.pg import get_pg_backend

            backend = get_pg_backend()
            connection_service = build_connection_service(
                backend.connection_store(tenant_id), tenant_id=tenant_id
            )
            # approval 的帧持久化在 loader frame_sink（批 2 写 interruptions 表），
            # broker 只承担进程内 pending + Event（重启后由恢复扫描器 restore 重建）。
            services = TenantServices(
                graph_store=backend.graph_store(tenant_id),
                recording_store=backend.recording_store(tenant_id),
                feedback_store=backend.feedback_store(tenant_id),
                message_service=MessageService(email_sender=get_smtp_sender(), webhook_sender=get_webhook_sender(), im_sender=get_im_sender()),
                approval_broker=ApprovalBroker(),
                debug_broker=DebuggerBroker(),
                cancellation_broker=RunCancellationBroker(),
                event_wait_broker=EventWaitBroker(),
                monitoring=backend.monitoring_store(tenant_id),
                run_store=backend.run_store(tenant_id),
                task_store=TaskStore(),
                routing_store=RoutingStore(),
                report_store=PgReportStore(backend.engine, tenant_id),
                shadow_store=ShadowStore(),
                memory_store=backend.memory_store(tenant_id),
                audit_store=backend.audit_store(tenant_id),
                connection_service=connection_service,
                channel_registry=build_channel_registry(
                    connection_service, tenant_id=tenant_id,
                    store=backend.channel_store(tenant_id),
                ),
                webhook_deliveries=PgDeliveryStore(backend.engine, tenant_id),
                openapi_imports=PgImportStore(backend.engine, tenant_id),
            )
            TenantRegistry._wire_alert_notifier(services)
            return services
        connection_service = build_connection_service(ConnectionStore(), tenant_id=tenant_id)
        services = TenantServices(
            graph_store=GraphStore(),
            recording_store=RecordingStore(),
            feedback_store=FeedbackStore(),
            message_service=MessageService(email_sender=get_smtp_sender(), webhook_sender=get_webhook_sender(), im_sender=get_im_sender()),
            approval_broker=ApprovalBroker(),
            debug_broker=DebuggerBroker(),
            cancellation_broker=RunCancellationBroker(),
            event_wait_broker=EventWaitBroker(),
            monitoring=MonitoringStore(),
            run_store=RunStore(),
            task_store=TaskStore(),
            routing_store=RoutingStore(),
            report_store=ReportStore(),
            shadow_store=ShadowStore(),
            memory_store=MemoryStore(),
            audit_store=AuditStore(),
            connection_service=connection_service,
            channel_registry=build_channel_registry(
                connection_service, tenant_id=tenant_id
            ),
            webhook_deliveries=InMemoryDeliveryStore(),
            openapi_imports=ImportStore(),
        )
        TenantRegistry._wire_alert_notifier(services)
        return services

    @staticmethod
    def _wire_alert_notifier(services: TenantServices) -> None:
        from atlas.monitoring.notify import AlertNotifier

        services.monitoring.set_notifier(
            AlertNotifier(services.message_service)
        )

    def reset_tenant(self, tenant_id: str) -> None:
        """本租户运行时数据重置：图/消息/审批/调试/监控/运行状态/灰度路由/批量回放报告/长期记忆清空，规则回默认；
        录制用例与反馈沿用「reset 不清除」语义保留；监控运行计数器不重置。"""
        services = self.get(tenant_id)
        services.graph_store.clear()
        services.message_service.reset()
        services.approval_broker.reset()
        services.debug_broker.reset()
        services.cancellation_broker.reset()
        services.event_wait_broker.reset()
        services.monitoring.reset()
        services.run_store.reset()
        services.routing_store.reset()
        services.report_store.reset()
        services.shadow_store.reset()
        services.memory_store.clear()
