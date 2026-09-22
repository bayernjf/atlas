from __future__ import annotations

import threading
from datetime import datetime, timezone

from pydantic import BaseModel

from .models import OperationDescriptor, ParsedSpec, SecurityScheme

MAX_SPECS_PER_TENANT = 5
MAX_OPERATIONS_PER_SPEC = 200


class ImportStoreError(Exception):
    """业务错误；status_code 供 HTTP 层折算，code 为稳定错误码。"""

    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class ImportedSpec(BaseModel):
    spec_id: str
    title: str
    base_url: str
    created_at: str
    operations: list[OperationDescriptor]
    security_schemes: dict[str, SecurityScheme] = {}
    credential_envelopes: dict[str, str] = {}


class ImportStore:
    """进程内 per-tenant 导入规格存储（docs/42 §1 B）。

    随 TenantServices 装配；照 connections 先例 reset 不清。
    """

    def __init__(self) -> None:
        self._specs: dict[str, ImportedSpec] = {}
        self._counter = 0
        self._lock = threading.Lock()

    def add(
        self,
        spec: ParsedSpec,
        *,
        now: datetime | None = None,
        envelopes: dict[str, str] | None = None,
    ) -> ImportedSpec:
        with self._lock:
            if len(self._specs) >= MAX_SPECS_PER_TENANT:
                raise ImportStoreError(
                    "OPENAPI_LIMIT_EXCEEDED",
                    f"每租户最多导入 {MAX_SPECS_PER_TENANT} 份 API 规格",
                )
            if len(spec.operations) > MAX_OPERATIONS_PER_SPEC:
                raise ImportStoreError(
                    "OPENAPI_LIMIT_EXCEEDED",
                    f"单份规格最多包含 {MAX_OPERATIONS_PER_SPEC} 个 operation",
                )
            self._counter += 1
            spec_id = f"openapi-{self._counter}"
            imported = ImportedSpec(
                spec_id=spec_id,
                title=spec.title,
                base_url=spec.base_url,
                created_at=(now or datetime.now(timezone.utc)).isoformat(),
                operations=[op for op in spec.operations if not op.skipped],
                security_schemes=spec.security_schemes,
                credential_envelopes=envelopes or {},
            )
            self._specs[spec_id] = imported
            return imported

    def list(self) -> list[ImportedSpec]:
        with self._lock:
            return list(self._specs.values())

    def get(self, spec_id: str) -> ImportedSpec | None:
        with self._lock:
            return self._specs.get(spec_id)

    def delete(self, spec_id: str) -> bool:
        with self._lock:
            if spec_id not in self._specs:
                return False
            del self._specs[spec_id]
            return True

    def put_credentials(
        self, spec_id: str, envelopes: dict[str, str]
    ) -> ImportedSpec | None:
        with self._lock:
            imported = self._specs.get(spec_id)
            if imported is None:
                return None
            imported.credential_envelopes = envelopes
            return imported
