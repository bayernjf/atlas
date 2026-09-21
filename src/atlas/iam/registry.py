"""按租户装配的进程内服务注册表（04 §5.14）。

每租户独立持有图/录制/反馈/消息/审批/调试/监控一套实例，id 计数各自从 1 起；
模板目录、适配器注册表、demo 店铺/mock 与出向连接为全局基础设施，不在此分区。
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

from atlas.collaboration.cancellations import RunCancellationBroker
from atlas.coordination import TaskStore
from atlas.message.service import MessageService
from atlas.recording import ReportStore, ShadowStore
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
    monitoring: MonitoringRepository
    run_store: RunRepository
    task_store: TaskStore
    routing_store: RoutingStore
    report_store: ReportStore  # D26 报告 v1：批量回放报告 ring（进程内，memory/PG 档均挂内存实例）
    shadow_store: ShadowStore  # D26 影子模式：旁路运行记录 ring（进程内，两档均挂内存实例，docs/33 §3）
    memory_store: MemoryRepository  # M11 长期记忆 fact/preference（批 3 PG 档换 PgMemoryStore）


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

    @staticmethod
    def _create_services(tenant_id: str) -> TenantServices:
        # M5a：八个进程内 store 统一自 storage.memory 构造（GraphStore/FeedbackStore
        # 已自 api/main.py 搬出，延迟导入环随之消除，顶层导入安全）。
        # M5b：ATLAS_STORAGE_BACKEND=pg 时，租户 store 换 PG 实现（per-tenant 绑定）；
        # debug 会话是短命临时态、其帧落库不在 U43–U45 验收，批 1 保持内存实现。
        if STORAGE_BACKEND == "pg":
            from atlas.storage.pg import get_pg_backend

            backend = get_pg_backend()
            # approval 的帧持久化在 loader frame_sink（批 2 写 interruptions 表），
            # broker 只承担进程内 pending + Event（重启后由恢复扫描器 restore 重建）。
            return TenantServices(
                graph_store=backend.graph_store(tenant_id),
                recording_store=backend.recording_store(tenant_id),
                feedback_store=backend.feedback_store(tenant_id),
                message_service=MessageService(),
                approval_broker=ApprovalBroker(),
                debug_broker=DebuggerBroker(),
                cancellation_broker=RunCancellationBroker(),
                monitoring=backend.monitoring_store(tenant_id),
                run_store=backend.run_store(tenant_id),
                task_store=TaskStore(),
                routing_store=RoutingStore(),
                report_store=ReportStore(),
                shadow_store=ShadowStore(),
                memory_store=backend.memory_store(tenant_id),
            )
        return TenantServices(
            graph_store=GraphStore(),
            recording_store=RecordingStore(),
            feedback_store=FeedbackStore(),
            message_service=MessageService(),
            approval_broker=ApprovalBroker(),
            debug_broker=DebuggerBroker(),
            cancellation_broker=RunCancellationBroker(),
            monitoring=MonitoringStore(),
            run_store=RunStore(),
            task_store=TaskStore(),
            routing_store=RoutingStore(),
            report_store=ReportStore(),
            shadow_store=ShadowStore(),
            memory_store=MemoryStore(),
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
        services.monitoring.reset()
        services.run_store.reset()
        services.routing_store.reset()
        services.report_store.reset()
        services.shadow_store.reset()
        services.memory_store.clear()
