# -*- coding: utf-8 -*-
"""litellm 首次导入必须串行在主线程（打包 BM，docs/18 试用干跑抓到的进程级缺陷）。

实测现象（2026-10-08，真容器复现两次）：配了模型但端点不可达时，第一次运行抛
`_DeadlockError: deadlock detected by _ModuleLock('litellm...')`，之后整个进程不再响应
任何请求（含未鉴权的 /api/health）。根因是进程内**首次** `import litellm` 发生在 worker
线程，而 litellm 在导入过程中就往 root logger 挂了含懒导入的过滤器，两条线程互相等对方
的模块锁。修法：起服务前先把包导完（`llm/decision.warm_litellm`）。

三条守护都不出网：假模块的 exec 会被计数，所以"导入根本没发生"这种空转一定会红。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from contextlib import contextmanager

from atlas.llm import decision

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "atlas"))


def _stub_module(name: str, on_exec) -> types.ModuleType:
    """造假模块：exec 时执行 on_exec（模拟 load_dotenv 注入，或直接抛错）。"""

    class _Loader:
        def create_module(self, spec):
            return module

        def exec_module(self, _mod):
            on_exec()

    module = types.ModuleType(name)
    module.__spec__ = importlib.util.spec_from_loader(name, _Loader())
    return module


@contextmanager
def _installed(name: str, module: types.ModuleType):
    """把假模块挂到 sys.meta_path 上，退出时按原样还原 sys.modules 与 meta_path。"""

    class _Finder:
        def find_spec(self, mod_name, path=None, target=None):  # noqa: ARG002
            return module.__spec__ if mod_name == name else None

    finder = _Finder()
    saved = sys.modules.pop(name, None)
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        sys.modules.pop(name, None)
        if saved is not None:
            sys.modules[name] = saved


def test_u1208_warm_imports_the_provider_without_touching_the_environment():
    """U1208：预热必须①真的把包导进来②一个环境变量都不留下③第二次不再导。"""
    calls: list[str] = []

    def on_exec() -> None:
        calls.append("exec")
        os.environ["LITELLM_MODEL"] = "openai/leaked-from-dotenv"
        os.environ["ATLAS_KEYS_MUST_NOT_SURVIVE"] = "injected"

    before = dict(os.environ)
    with _installed("litellm", _stub_module("litellm", on_exec)):
        decision.warm_litellm()
        assert calls == ["exec"], "假模块没被执行＝预热压根没导入，这条守护在空转"
        assert "litellm" in sys.modules
        decision.warm_litellm()
        assert calls == ["exec"], "幂等破了：第二次又走了一遍导入"
    assert dict(os.environ) == before, (
        f"预热污染了进程环境：{sorted(set(os.environ.items()) ^ set(before.items()))}"
    )


def test_u1209_src_never_imports_litellm_at_module_level() -> None:
    """U1209：src/atlas 里 litellm 只许在函数体内导入。

    理由见 tests/conftest.py 的 `_no_real_llm_provider` 注释——litellm 在 import 期自己调
    load_dotenv()，只要有一个用例先把它导进来，其后所有用例的决策器都变成真 LLM 客户端，
    整套的表现是"卡死"而不是"失败"。顶层导入会在收集阶段就跨过那条线，所以必须机检。
    """
    offenders = []
    for root, dirs, files in os.walk(_SRC):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as handle:
                for lineno, line in enumerate(handle, start=1):
                    stripped = line.lstrip()
                    if not (stripped.startswith("import litellm")
                            or stripped.startswith("from litellm")):
                        continue
                    if line[:1] in (" ", "\t"):
                        continue  # 缩进＝函数体内懒导入，正是约定形态
                    offenders.append(f"{os.path.relpath(path, _SRC)}:{lineno}")
    assert not offenders, f"litellm 被提到模块顶层，会在 import 期污染整个测试进程：{offenders}"


def _top_level_offenders(tree_root: str) -> list[str]:
    found = []
    for root, _dirs, files in os.walk(tree_root):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as handle:
                for lineno, line in enumerate(handle, start=1):
                    stripped = line.lstrip()
                    if (stripped.startswith("import litellm")
                            and line[:1] not in (" ", "\t")):
                        found.append(f"{path}:{lineno}")
    return found


def test_u1210_the_scanner_catches_a_planted_top_level_import(tmp_path) -> None:
    """反向门：植一段顶层导入进合成源码，扫描必须只报那一行（第 6 行的函数内导入不算）。"""
    bad = tmp_path / "atlas" / "llm" / "bad.py"
    bad.parent.mkdir(parents=True)
    bad.write_text("# 顶部注释\n\nimport litellm\n\n\ndef use():\n    import litellm\n",
                   encoding="utf-8")
    assert _top_level_offenders(str(tmp_path)) == [f"{bad}:3"]
    (tmp_path / "atlas" / "llm" / "bad.py").write_text(
        "def use():\n    import litellm\n", encoding="utf-8")
    assert _top_level_offenders(str(tmp_path)) == [], "全函数内导入时扫描器不该报任何东西（正控）"


def test_u1211_lifespan_warms_before_the_app_starts_serving() -> None:
    """U1211：预热挂在 lifespan 上、且在 yield 之前——晚一步就还是并发首导入。"""
    import inspect

    from atlas.api import main

    source = inspect.getsource(main.lifespan)
    assert "warm_litellm()" in source
    assert source.index("warm_litellm()") < source.index("yield"), "导在开始服务之后就晚了"


def test_u1212_a_broken_provider_cannot_break_boot(monkeypatch) -> None:
    """U1212：包导不起来时启动不许连带炸——"没模型也能跑"是既有行为，不能倒退。"""
    boom: dict[str, int] = {}

    def on_exec() -> None:
        boom["raised"] = 1
        raise ImportError("litellm is not installed")

    stub = _stub_module("litellm", on_exec)
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    before = dict(os.environ)
    with _installed("litellm", stub):
        decision.warm_litellm()  # 不许抛
        left_behind = sys.modules.get("litellm")
    assert boom.get("raised") == 1, "假模块没抛上去＝这条路径没被走到，守护空转"
    assert left_behind is not stub, "失败的导入必须从 sys.modules 清干净，不能留下半成品模块"
    assert dict(os.environ) == before
