"""按租户装配的进程内服务注册表（04 §5.14）。

每租户独立持有图/录制/反馈/消息/审批/调试/监控一套实例，id 计数各自从 1 起；
模板目录、适配器注册表、demo 店铺/mock 与出向连接为全局基础设施，不在此分区。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from atlas.message.service import MessageService
from atlas.storage.base import (
    ApprovalRepository,
    DebugRepository,
    FeedbackRepository,
    GraphRepository,
    MonitoringRepository,
    RecordingRepository,
)
from atlas.storage.memory import (
    ApprovalBroker,
    DebuggerBroker,
    FeedbackStore,
    GraphStore,
    MonitoringStore,
    RecordingStore,
)


@dataclass
class TenantServices:
    graph_store: GraphRepository
    recording_store: RecordingRepository
    feedback_store: FeedbackRepository
    message_service: object  # MessageService 是服务非存储，不进 Repository 抽象（docs/24 §1.1）
    approval_broker: ApprovalRepository
    debug_broker: DebugRepository
    monitoring: MonitoringRepository


class TenantRegistry:
    def __init__(self) -> None:
        self._tenants: dict[str, TenantServices] = {}
        self._lock = threading.Lock()

    def get(self, tenant_id: str) -> TenantServices:
        with self._lock:
            services = self._tenants.get(tenant_id)
            if services is None:
                services = self._create_services()
                self._tenants[tenant_id] = services
            return services

    @staticmethod
    def _create_services() -> TenantServices:
        # 八个进程内 store 统一自 storage.memory 构造（M5a：GraphStore/FeedbackStore
        # 已自 api/main.py 搬出，延迟导入环随之消除，顶层导入安全）。
        return TenantServices(
            graph_store=GraphStore(),
            recording_store=RecordingStore(),
            feedback_store=FeedbackStore(),
            message_service=MessageService(),
            approval_broker=ApprovalBroker(),
            debug_broker=DebuggerBroker(),
            monitoring=MonitoringStore(),
        )

    def reset_tenant(self, tenant_id: str) -> None:
        """本租户运行时数据重置：图/消息/审批/调试/监控清空，规则回默认；
        录制与反馈沿用「reset 不清除」语义保留；监控运行计数器不重置。"""
        services = self.get(tenant_id)
        services.graph_store.clear()
        services.message_service.reset()
        services.approval_broker.reset()
        services.debug_broker.reset()
        services.monitoring.reset()
