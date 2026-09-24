from __future__ import annotations

import threading
from datetime import datetime, timezone

from pydantic import BaseModel

from .models import OperationDescriptor, ParsedSpec, SecurityScheme

MAX_SPECS_PER_TENANT = 5
MAX_OPERATIONS_PER_SPEC = 200


class ImportStoreError(Exception):
    """业务错误；status_code 供 HTTP 层折算，code 为稳定错误码。"""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 422,
        existing_spec_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.existing_spec_id = existing_spec_id


class ImportedSpec(BaseModel):
    spec_id: str
    title: str
    base_url: str
    created_at: str
    operations: list[OperationDescriptor]
    security_schemes: dict[str, SecurityScheme] = {}
    credential_envelopes: dict[str, str] = {}
    # docs/56 §3：内容指纹去重 + 软删除（纯超集，旧实例缺省为 ""/None）
    content_hash: str = ""
    deleted_at: str | None = None


class ImportStore:
    """进程内 per-tenant 导入规格存储（docs/42 §3 B；docs/56 §3 去重/软删）。

    随 TenantServices 装配；照 connections 先例 reset 不清。软删除以 deleted_at
    字段模拟：list/get/名额统计只看未删规格，同内容（content_hash）未删规格拒绝重复导入。
    """

    def __init__(self) -> None:
        self._specs: dict[str, ImportedSpec] = {}
        self._counter = 0
        self._lock = threading.Lock()

    def _active(self) -> list[ImportedSpec]:
        return [s for s in self._specs.values() if s.deleted_at is None]

    def add(
        self,
        spec: ParsedSpec,
        *,
        now: datetime | None = None,
        envelopes: dict[str, str] | None = None,
    ) -> ImportedSpec:
        if len(spec.operations) > MAX_OPERATIONS_PER_SPEC:
            raise ImportStoreError(
                "OPENAPI_LIMIT_EXCEEDED",
                f"单份规格最多包含 {MAX_OPERATIONS_PER_SPEC} 个 operation",
            )
        fingerprint = spec.content_fingerprint()
        with self._lock:
            # docs/56：同租户存在未删的同指纹规格 → 409，带 existingSpecId
            duplicate = next(
                (s for s in self._active() if s.content_hash == fingerprint),
                None,
            )
            if duplicate is not None:
                raise ImportStoreError(
                    "OPENAPI_DUPLICATE",
                    f"该 API 规格已导入（{duplicate.spec_id}：{duplicate.title}）",
                    status_code=409,
                    existing_spec_id=duplicate.spec_id,
                )
            if len(self._active()) >= MAX_SPECS_PER_TENANT:
                raise ImportStoreError(
                    "OPENAPI_LIMIT_EXCEEDED",
                    f"每租户最多导入 {MAX_SPECS_PER_TENANT} 份 API 规格",
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
                content_hash=fingerprint,
            )
            self._specs[spec_id] = imported
            return imported

    def list(self, *, include_deleted: bool = False) -> list[ImportedSpec]:
        """默认仅未删（零回归）；include_deleted=True 返回全部（含已软删，按导入序）。"""
        with self._lock:
            if include_deleted:
                return list(self._specs.values())
            return self._active()

    def get(self, spec_id: str) -> ImportedSpec | None:
        with self._lock:
            spec = self._specs.get(spec_id)
            return spec if spec is not None and spec.deleted_at is None else None

    def delete(self, spec_id: str) -> bool:
        """软删：未删 → 置 deleted_at 返回 True；不存在或已删 → False。"""
        with self._lock:
            spec = self._specs.get(spec_id)
            if spec is None or spec.deleted_at is not None:
                return False
            spec.deleted_at = datetime.now(timezone.utc).isoformat()
            return True

    def purge(self, spec_id: str) -> bool:
        """物理删除（docs/60 G2）：仅已软删记录可彻底删除。

        - 记录不存在：返回 False（路由折算 404）；
        - 存在但未软删：抛 OPENAPI_NOT_SOFT_DELETED（路由折算 409，提示先软删）；
        - 已软删：物理移除并返回 True。凭证列与同行天然级联，无独立凭证表。
        """
        with self._lock:
            spec = self._specs.get(spec_id)
            if spec is None:
                return False
            if spec.deleted_at is None:
                raise ImportStoreError(
                    "OPENAPI_NOT_SOFT_DELETED",
                    f"API 规格 {spec_id} 尚未软删除，请先删除再彻底删除",
                    status_code=409,
                )
            del self._specs[spec_id]
            return True

    def restore(
        self, spec_id: str
    ) -> tuple[bool, str | None, str | None]:
        """恢复软删规格。

        返回 (ok, code, existing_spec_id)：
        - 成功恢复：(True, None, None)；
        - 不存在或本就未删（无可恢复项）：(False, None, None) → API 404；
        - 恢复后与另一未删同指纹规格冲突：(False, "OPENAPI_DUPLICATE", 冲突 id) → API 409。
        """
        with self._lock:
            spec = self._specs.get(spec_id)
            if spec is None or spec.deleted_at is None:
                return False, None, None
            clash = next(
                (
                    s
                    for s in self._active()
                    if s.content_hash == spec.content_hash
                ),
                None,
            )
            if clash is not None:
                return False, "OPENAPI_DUPLICATE", clash.spec_id
            spec.deleted_at = None
            return True, None, None

    def put_credentials(
        self, spec_id: str, envelopes: dict[str, str]
    ) -> ImportedSpec | None:
        with self._lock:
            imported = self._specs.get(spec_id)
            if imported is None or imported.deleted_at is not None:
                return None
            imported.credential_envelopes = envelopes
            return imported
