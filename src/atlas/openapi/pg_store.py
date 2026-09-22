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

_COLS = "id, tenant_id, seq, title, base_url, created_at, operations"


class PgImportStore:
    """导入规格 PG 实现（docs/43 §1 B）。

    与 ImportStore 同方法形状；id 用全局 storage_id_seq（openapi-N），
    seq 存数字部分供同租户排序；行内 tenant_id 过滤；demo reset 不清本表。
    """

    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    @staticmethod
    def _row_to_spec(row: Any) -> ImportedSpec:
        # 列序：0 id,1 tenant_id,2 seq,3 title,4 base_url,5 created_at,6 operations
        raw = row[6]
        operations = json.loads(raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False))
        created = row[5]
        if isinstance(created, datetime):
            created = created.isoformat()
        return ImportedSpec(
            spec_id=row[0],
            title=row[3],
            base_url=row[4],
            created_at=created,
            operations=operations,
        )

    def add(self, spec: ParsedSpec, *, now: datetime | None = None) -> ImportedSpec:
        if len(spec.operations) > MAX_OPERATIONS_PER_SPEC:
            raise ImportStoreError(
                "OPENAPI_LIMIT_EXCEEDED",
                f"单份规格最多包含 {MAX_OPERATIONS_PER_SPEC} 个 operation",
            )
        operations = [op for op in spec.operations if not op.skipped]
        moment = now or datetime.now(timezone.utc)
        with self._engine.begin() as db:
            count = int(
                db.execute(
                    text("SELECT COUNT(*) FROM openapi_imports WHERE tenant_id = :t"),
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
            )
            db.execute(
                text(
                    "INSERT INTO openapi_imports (id, tenant_id, seq, title, base_url, "
                    "created_at, operations) VALUES (:id, :tenant_id, :seq, :title, "
                    ":base_url, :created_at, :operations)"
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
                },
            )
        return imported

    def list(self) -> list[ImportedSpec]:
        sql = f"SELECT {_COLS} FROM openapi_imports WHERE tenant_id = :t ORDER BY seq"
        with self._engine.connect() as db:
            rows = db.execute(text(sql), {"t": self._tenant_id}).all()
        return [self._row_to_spec(row) for row in rows]

    def get(self, spec_id: str) -> ImportedSpec | None:
        sql = f"SELECT {_COLS} FROM openapi_imports WHERE tenant_id = :t AND id = :id"
        with self._engine.connect() as db:
            row = db.execute(text(sql), {"t": self._tenant_id, "id": spec_id}).first()
        return self._row_to_spec(row) if row else None

    def delete(self, spec_id: str) -> bool:
        with self._engine.begin() as db:
            result = db.execute(
                text("DELETE FROM openapi_imports WHERE tenant_id = :t AND id = :id"),
                {"t": self._tenant_id, "id": spec_id},
            )
            return bool(result.rowcount)
