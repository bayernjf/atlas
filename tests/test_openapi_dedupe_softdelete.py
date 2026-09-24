# -*- coding: utf-8 -*-
"""docs/56 §3：OpenAPI 导入内容指纹去重 + 软删除/恢复（进程内 ImportStore，无需 PG）。"""

from __future__ import annotations

import pytest

from atlas.openapi.models import OperationDescriptor, ParsedSpec, SecurityScheme
from atlas.openapi.store import (
    MAX_SPECS_PER_TENANT,
    ImportStore,
    ImportStoreError,
)


def _spec(title="T", base_url="https://a.example.com", paths=("/x", "/y")) -> ParsedSpec:
    return ParsedSpec(
        title=title,
        base_url=base_url,
        operations=[
            OperationDescriptor(name=f"op{i}", method="get", path=path)
            for i, path in enumerate(paths)
        ],
    )


def test_fingerprint_stable_and_order_independent_in_dict_keys():
    s1 = _spec()
    s2 = _spec()
    assert s1.content_fingerprint() == s2.content_fingerprint()
    # 安全方案 dict 键顺序不同不影响指纹（sort_keys）
    a = _spec()
    a.security_schemes = {
        "k1": SecurityScheme(name="k1", kind="api_key", location="header", param="X-1"),
        "k2": SecurityScheme(name="k2", kind="bearer", location=None, param=""),
    }
    b = _spec()
    b.security_schemes = {
        "k2": SecurityScheme(name="k2", kind="bearer", location=None, param=""),
        "k1": SecurityScheme(name="k1", kind="api_key", location="header", param="X-1"),
    }
    assert a.content_fingerprint() == b.content_fingerprint()


def test_fingerprint_changes_with_content():
    base = _spec()
    assert _spec(base_url="https://b.example.com").content_fingerprint() != base.content_fingerprint()
    assert _spec(paths=("/x", "/z")).content_fingerprint() != base.content_fingerprint()
    assert _spec(title="Other").content_fingerprint() != base.content_fingerprint()


def test_duplicate_import_rejected_with_existing_id():
    store = ImportStore()
    first = store.add(_spec())
    with pytest.raises(ImportStoreError) as exc:
        store.add(_spec())
    assert exc.value.code == "OPENAPI_DUPLICATE"
    assert exc.value.status_code == 409
    assert exc.value.existing_spec_id == first.spec_id
    # 仅一条，名额未被重复占用
    assert len(store.list()) == 1


def test_soft_delete_hides_and_frees_quota_and_reenables_import():
    store = ImportStore()
    first = store.add(_spec())
    assert store.delete(first.spec_id) is True
    assert store.get(first.spec_id) is None
    assert store.list() == []
    assert store.delete(first.spec_id) is False  # 已删再删
    # 软删释放名额且同内容可重新导入
    reimported = store.add(_spec())
    assert reimported.spec_id != first.spec_id
    assert store.get(reimported.spec_id) is not None
    # 软删行不可写凭证
    assert store.put_credentials(first.spec_id, {}) is None


def test_quota_counts_only_active():
    store = ImportStore()
    specs = [store.add(_spec(title=f"T{i}", base_url=f"https://{i}.example.com")) for i in range(MAX_SPECS_PER_TENANT)]
    # 已满
    with pytest.raises(ImportStoreError) as exc:
        store.add(_spec(title="overflow", base_url="https://overflow.example.com"))
    assert exc.value.code == "OPENAPI_LIMIT_EXCEEDED"
    # 删一条（软删）后名额释放，可再导
    store.delete(specs[0].spec_id)
    extra = store.add(_spec(title="overflow", base_url="https://overflow.example.com"))
    assert extra.spec_id is not None
    assert len(store.list()) == MAX_SPECS_PER_TENANT


def test_restore_success_conflict_and_missing():
    store = ImportStore()
    a = store.add(_spec())
    assert store.delete(a.spec_id) is True
    assert store.restore(a.spec_id) == (True, None, None)
    assert store.get(a.spec_id) is not None

    # 删 A → 导同内容为 B（未删）→ 恢复 A 冲突，回传 B
    assert store.delete(a.spec_id) is True
    b = store.add(_spec())
    ok, code, existing = store.restore(a.spec_id)
    assert ok is False and code == "OPENAPI_DUPLICATE" and existing == b.spec_id
    assert store.get(a.spec_id) is None  # 冲突未恢复

    # 不存在 / 对未删项恢复 → (False, None, None)
    assert store.restore("openapi-nope") == (False, None, None)
    assert store.restore(b.spec_id) == (False, None, None)


def test_list_include_deleted_returns_soft_deleted_with_flag():
    """docs/60 G2：list 默认仅未删；include_deleted=True 含已软删（含 deleted_at）。"""
    store = ImportStore()
    a = store.add(_spec(title="A", paths=("/a",)))
    b = store.add(_spec(title="B", paths=("/b",)))
    assert store.delete(a.spec_id) is True

    active = store.list()
    assert [s.spec_id for s in active] == [b.spec_id]

    all_specs = store.list(include_deleted=True)
    assert {s.spec_id for s in all_specs} == {a.spec_id, b.spec_id}
    deleted = next(s for s in all_specs if s.spec_id == a.spec_id)
    assert deleted.deleted_at is not None
    assert b.deleted_at is None


def test_purge_requires_soft_delete_then_physically_removes():
    """docs/60 G2：purge 未软删→409 OPENAPI_NOT_SOFT_DELETED；不存在→False；
    已软删→物理移除 True，且 include_deleted 列表也不再含、指纹可重新导入。"""
    store = ImportStore()
    a = store.add(_spec(title="A", paths=("/a",)))

    # 未软删直接 purge → 409 业务错误
    with pytest.raises(ImportStoreError) as exc:
        store.purge(a.spec_id)
    assert exc.value.code == "OPENAPI_NOT_SOFT_DELETED"
    assert exc.value.status_code == 409
    # 未物理删除，仍在未删列表
    assert [s.spec_id for s in store.list()] == [a.spec_id]

    # 不存在 → False
    assert store.purge("openapi-nope") is False

    # 软删后 purge → True，物理消失
    assert store.delete(a.spec_id) is True
    assert store.purge(a.spec_id) is True
    assert store.list(include_deleted=True) == []
    # 物理删除后同指纹可重新导入（不再判重）
    again = store.add(_spec(title="A", paths=("/a",)))
    assert again.spec_id != a.spec_id
