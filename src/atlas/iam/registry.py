"""按租户装配的进程内服务注册表（04 §5.14）。

每租户独立持有图/录制/反馈/消息/审批/调试/监控一套实例，id 计数各自从 1 起；
模板目录、适配器注册表、demo 店铺/mock 与出向连接为全局基础设施，不在此分区。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass
class TenantServices:
    graph_store: object
    recording_store: object
    feedback_store: object
    message_service: object
    approval_broker: object
    debug_broker: object
    monitoring: object


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
        # 延迟导入：GraphStore/FeedbackStore 内联在 api/main.py，
        # 顶层导入会与 main → iam.registry 形成环；请求期模块已加载完毕。
        from atlas.api.main import FeedbackStore, GraphStore
        from atlas.collaboration.approvals import ApprovalBroker
        from atlas.debug.sessions import DebuggerBroker
        from atlas.message.service import MessageService
        from atlas.monitoring.records import MonitoringStore
        from atlas.recording.cases import RecordingStore

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
