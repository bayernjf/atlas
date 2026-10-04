# -*- coding: utf-8 -*-
"""prod 存储档位的启动门（docs/89 §16 A-11；U1138–U1139）。

这条门**不新增规矩**，它执行一条早已写着的规矩：`.env.example:28` 写了"prod 必须 pg，
否则全部状态落在内存、重启即失"，可全仓没有任何地方检查——忘配或配错时进程照常起来、
`/api/ready` 照常 200，直到下一次重启把图、运行记录、审批与调度认领一起清掉。
同族先例＝docs/77 R4（非法档位值拒启）与 docs/66（prod 缺引导口令拒启）。

所以这里最要紧的一条不是"拒了"，而是**三条合法出路都在**：`pg`／演示面／显式署名 opt-in；
再加一条 dev 缺省形态不变的对照，否则"把所有人都拦了"也能让上面那几条全绿。
"""

from __future__ import annotations

import pytest

from atlas.security.bootstrap import (
    VOLATILE_STORAGE_OPT_IN_ENV,
    assert_prod_storage_backend,
    read_storage_backend,
)


def _env(monkeypatch: pytest.MonkeyPatch, **kv: str | None) -> None:
    """先清掉继承来的相关档位，再按用例设置——否则本机 .env 会让断言随人而异。"""
    for name in ("ATLAS_ENV", "ATLAS_STORAGE_BACKEND", "ATLAS_ENABLE_DEMO_MOCK",
                 VOLATILE_STORAGE_OPT_IN_ENV):
        monkeypatch.delenv(name, raising=False)
    for name, value in kv.items():
        if value is not None:
            monkeypatch.setenv(name, value)


def test_u1138_prod_without_pg_refuses_to_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, ATLAS_ENV="prod")  # 存储档缺省＝memory
    assert read_storage_backend() == "memory"
    with pytest.raises(RuntimeError) as exc:
        assert_prod_storage_backend()
    # 拒启原因必须点名可操作的配置项，只说"配置不安全"等于让运维重新猜一遍
    assert "ATLAS_STORAGE_BACKEND=pg" in str(exc.value)
    assert "/api/ready" in str(exc.value)


@pytest.mark.parametrize(
    "kv",
    [
        {"ATLAS_ENV": "prod", "ATLAS_STORAGE_BACKEND": "pg"},  # 正常生产形态
        {"ATLAS_ENV": "prod", "ATLAS_ENABLE_DEMO_MOCK": "1"},  # 演示面：与 shop/database 同一判定
        {"ATLAS_ENV": "prod", VOLATILE_STORAGE_OPT_IN_ENV: "1"},  # 明知易失，署名承担
    ],
)
def test_u1139_three_explicit_exits_still_boot(
    monkeypatch: pytest.MonkeyPatch, kv: dict[str, str]
) -> None:
    _env(monkeypatch, **kv)
    assert_prod_storage_backend()  # 不抛＝放行


def test_u1139_dev_shape_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """判别对照：dev 不配存储档照常起（这条门不能把本地形态一起锁死）。"""
    _env(monkeypatch, ATLAS_ENV="dev")
    assert read_storage_backend() == "memory"
    assert_prod_storage_backend()


def test_illegal_backend_still_raises_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """R4 那条不被本门取代：prod 下非法档位值仍是 ValueError，不是"当成 memory 放行"。"""
    _env(monkeypatch, ATLAS_ENV="prod", ATLAS_STORAGE_BACKEND="postgres")
    with pytest.raises(ValueError, match="ATLAS_STORAGE_BACKEND 非法值"):
        assert_prod_storage_backend()
