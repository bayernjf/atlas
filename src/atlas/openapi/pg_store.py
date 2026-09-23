from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, text

from .models import ParsedSpec
from .store import (
    MAX_OPERATIONS_PER_SPEC,
    MAX_SPECS_PER_TENANT,
    ImportedSpec,
    ImportStoreError,
)

_COLS = (
    "id, tenant_id, seq, title, base_url, created_at, operations, "
    "security_schemes, credential_envelopes, content_hash, deleted_at"
)


class PgImportStore:
    """导入规格 PG 实现（docs/43 §1 B；docs/56 §3 去重/软删）。

    与 ImportStore 同方法形状；id 用全局 storage_id_seq（openapi-N），
    seq 存数字部分供同租户排序；行内 tenant_id 过滤；demo reset 不清本表。
    去重/软删以 content_hash 与 deleted_at 列实现（部分索引见迁移 023）。
    """

    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    @staticmethod
    def _row_to_spec(row: Any) -> ImportedSpec:
        # 列序：0 id,1 tenant_id,2 seq,3 title,4 base_url,5 created_at,
        # 6 operations,7 security_schemes,8 credential_envelopes,
        # 9 content_hash,10 deleted_at
        def _jsonb(value: Any) -> Any:
            return json.loads(value) if isinstance(value, str) else value

        operations = _jsonb(row[6])
        if operations is None:
            operations = []
        schemes = _jsonb(row[7]) or {}
        envelopes = _jsonb(row[8]) or {}
        created = row[5]
        if isinstance(created, datetime):
            created = created.isoformat()
        return ImportedSpec(
            spec_id=row[0],
            title=row[3],
            base_url=row[4],
            created_at=created,
            operations=operations,
            security_schemes=schemes,
            credential_envelopes=envelopes,
            content_hash=row[9] or "",
            deleted_at=row[10],
        )

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
        operations = [op for op in spec.operations if not op.skipped]
        fingerprint = spec.content_fingerprint()
        moment = now or datetime.now(timezone.utc)
        envelope_map = envelopes or {}
        with self._engine.begin() as db:
            duplicate = db.execute(
                text(
                    "SELECT id, title FROM openapi_imports "
                    "WHERE tenant_id = :t AND deleted_at IS NULL AND content_hash = :h "
                    "LIMIT 1"
                ),
                {"t": self._tenant_id, "h": fingerprint},
            ).first()
            if duplicate is not None:
                raise ImportStoreError(
                    "OPENAPI_DUPLICATE",
                    f"该 API 规格已导入（{duplicate[0]}：{duplicate[1]}）",
                    status_code=409,
                    existing_spec_id=duplicate[0],
                )
            count = int(
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM openapi_imports "
                        "WHERE tenant_id = :t AND deleted_at IS NULL"
                    ),
                    {"t": self._tenant_id},
                ).scalar_one()
            )
            if count >= MAX_SPECS_PER_TENANT:
                raise ImportStoreError(
                    "OPENAPI_LIMIT_EXCEEDED",
                    f"每租户最多导入 {MAX_SPECS_PER_TENANT} 份 API 规格",
                )
            seq = int(db.execute(text("SELECT nextval('storage_id_seq')")).scalar_one())
            spec_id = f"openapi-{seq}"
            imported = ImportedSpec(
                spec_id=spec_id,
                title=spec.title,
                base_url=spec.base_url,
                created_at=moment.isoformat(),
                operations=operations,
                security_schemes=spec.security_schemes,
                credential_envelopes=envelope_map,
                content_hash=fingerprint,
            )
            db.execute(
                text(
                    "INSERT INTO openapi_imports (id, tenant_id, seq, title, base_url, "
                    "created_at, operations, security_schemes, credential_envelopes, "
                    "content_hash, deleted_at) "
                    "VALUES (:id, :tenant_id, :seq, :title, :base_url, :created_at, "
                    ":operations, :security_schemes, :credential_envelopes, :content_hash, NULL)"
                ),
                {
                    "id": spec_id,
                    "tenant_id": self._tenant_id,
                    "seq": seq,
                    "title": spec.title,
                    "base_url": spec.base_url,
                    "created_at": moment,
                    "operations": json.dumps(
                        [op.model_dump() for op in operations], ensure_ascii=False
                    ),
                    "security_schemes": json.dumps(
                        {
                            name: scheme.model_dump()
                            for name, scheme in spec.security_schemes.items()
                        },
                        ensure_ascii=False,
                    ),
                    "credential_envelopes": json.dumps(envelope_map, ensure_ascii=False),
                    "content_hash": fingerprint,
                },
            )
        return imported

    def list(self) -> list[ImportedSpec]:
        sql = (
            f"SELECT {_COLS} FROM openapi_imports "
            "WHERE tenant_id = :t AND deleted_at IS NULL ORDER BY seq"
        )
        with self._engine.connect() as db:
            rows = db.execute(text(sql), {"t": self._tenant_id}).all()
        return [self._row_to_spec(row) for row in rows]

    def get(self, spec_id: str) -> ImportedSpec | None:
        sql = (
            f"SELECT {_COLS} FROM openapi_imports "
            "WHERE tenant_id = :t AND id = :id AND deleted_at IS NULL"
        )
        with self._engine.connect() as db:
            row = db.execute(text(sql), {"t": self._tenant_id, "id": spec_id}).first()
        return self._row_to_spec(row) if row else None

    def delete(self, spec_id: str) -> bool:
        """软删：仅未删行受影响，返回是否有行被更新。"""
        with self._engine.begin() as db:
            result = db.execute(
                text(
                    "UPDATE openapi_imports SET deleted_at = :now "
                    "WHERE tenant_id = :t AND id = :id AND deleted_at IS NULL"
                ),
                {
                    "now": datetime.now(timezone.utc).isoformat(),
                    "t": self._tenant_id,
                    "id": spec_id,
                },
            )
            return bool(result.rowcount)

    def restore(
        self, spec_id: str
    ) -> tuple[bool, str | None, str | None]:
        """恢复软删规格，返回 (ok, code, existing_spec_id)，语义同 ImportStore.restore。"""
        with self._engine.begin() as db:
            row = db.execute(
                text(
                    f"SELECT {_COLS} FROM openapi_imports "
                    "WHERE tenant_id = :t AND id = :id"
                ),
                {"t": self._tenant_id, "id": spec_id},
            ).first()
            if row is None or row[10] is None:
                return False, None, None
            target = self._row_to_spec(row)
            clash = db.execute(
                text(
                    "SELECT id FROM openapi_imports "
                    "WHERE tenant_id = :t AND deleted_at IS NULL "
                    "AND content_hash = :h AND id != :id LIMIT 1"
                ),
                {"t": self._tenant_id, "h": target.content_hash, "id": spec_id},
            ).first()
            if clash is not None:
                return False, "OPENAPI_DUPLICATE", clash[0]
            db.execute(
                text(
                    "UPDATE openapi_imports SET deleted_at = NULL "
                    "WHERE tenant_id = :t AND id = :id"
                ),
                {"t": self._tenant_id, "id": spec_id},
            )
            return True, None, None

    def put_credentials(
        self, spec_id: str, envelopes: dict[str, str]
    ) -> ImportedSpec | None:
        with self._engine.begin() as db:
            result = db.execute(
                text(
                    "UPDATE openapi_imports SET credential_envelopes = :envelopes "
                    "WHERE tenant_id = :t AND id = :id AND deleted_at IS NULL"
                ),
                {
                    "envelopes": json.dumps(envelopes, ensure_ascii=False),
                    "t": self._tenant_id,
                    "id": spec_id,
                },
            )
            if not result.rowcount:
                return None
        return self.get(spec_id)
