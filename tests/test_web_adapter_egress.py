# -*- coding: utf-8 -*-
"""浏览器 `navigate` 与页面内请求的出向闸门（docs/32 同族；N3 复开的前置，U925·U1125–U1129）。

这批不接 `web-playwright` 进 prod 运行期注册表（docs/89 §14 的 N-1 定论：真浏览器只在演示面
装配），本文件也不启动浏览器；"真 Chromium 到底拦不拦得住"由 I21–I23 用真浏览器验。
堵的通道是"工具参数里的 url 原样交给 `page.goto()`"这条绕过现有 SSRF 防护的路——
理由写进 docs/08 §八 E 组：**httpx 的 denylist 管不住一个真浏览器**。

所以这里最要紧的一条不是"被拒"，而是**被拒时一次都没碰浏览器**（`goto` 没被调用）；
再加一组正向对照，否则"所有 url 一律失败"也能让上面那几条全绿。
"""

from __future__ import annotations

import pytest

from atlas.harness.base import ActionRequest, ActionStatus, Permission
from atlas.security.egress import EgressGuard
from atlas.web.adapter import WebHarnessAdapter

# navigate 声明为 WRITE，而 HarnessAdapter 缺省只给 READ ⇒ 不授权就永远测不到闸门
_WRITE = {Permission.READ, Permission.WRITE}


class _FakePage:
    """只记录 goto 的假页面——本文件不启动任何浏览器。"""

    def __init__(self) -> None:
        self.goto_calls: list[str] = []

    def goto(self, url: str, **_kw: object) -> None:  # noqa: ARG002
        self.goto_calls.append(url)

    @property
    def url(self) -> str:
        return self.goto_calls[-1] if self.goto_calls else "about:blank"


def _adapter(permit: tuple[str, ...] = (), allow: tuple[str, ...] = ()) -> tuple[WebHarnessAdapter, _FakePage]:
    page = _FakePage()
    guard = EgressGuard(allow, resolver=lambda host: ["8.8.8.8"], permit_cidrs=permit)
    adapter = WebHarnessAdapter(egress=guard, granted_permissions=_WRITE)
    adapter.page = page
    return adapter, page


def _navigate(adapter: WebHarnessAdapter, url: str):
    return adapter.execute(ActionRequest(capability_name="navigate", parameters={"url": url}))


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",   # 云元数据
    "http://127.0.0.1:8000/api/graphs",           # 回环
    "http://10.0.0.5/internal",                   # 私网
    "http://[fd00::1]/internal",                  # 唯一本地 IPv6
    "file:///etc/passwd",                         # 非 http 方案
    "data:text/html,<script>alert(1)</script>",
    "http://2130706433/",                         # 十进制整数写法（同 127.0.0.1）
])
def test_u925_blocked_targets_never_touch_the_browser(url: str) -> None:
    adapter, page = _adapter()
    result = _navigate(adapter, url)
    assert result.status is ActionStatus.FAILED, f"{url} 竟然放行了"
    assert result.error is not None and result.error.code.startswith("EGRESS_"), result.error
    assert page.goto_calls == [], "闸门只报错没拦住：浏览器仍然被叫去访问该地址"


def test_u925_positive_control_a_public_target_reaches_the_browser() -> None:
    """判别对照：放行的那条必须真的走到 `goto`。

    没有这条，上面一整排断言可以因为"任何 url 都失败"而全绿——那等于把闸门焊死在关闭位。
    """
    adapter, page = _adapter()
    result = _navigate(adapter, "http://8.8.8.8/")
    assert result.status is ActionStatus.SUCCESS, result.error
    assert page.goto_calls == ["http://8.8.8.8/"]


def test_u925_allowlist_still_restricts_and_loopback_needs_the_code_seam() -> None:
    """与 httpapi 同一份策略的两个性质：白名单生效、回环只能由**代码注入缝**放行。"""
    adapter, page = _adapter(allow=("example.com",))
    assert _navigate(adapter, "http://example.com/x").status is ActionStatus.SUCCESS
    denied = _navigate(adapter, "http://other.example.org/x")
    assert denied.status is ActionStatus.FAILED and "白名单" in str(denied.error.message)
    assert page.goto_calls == ["http://example.com/x"]

    strict, _ = _adapter()
    assert _navigate(strict, "http://127.0.0.1:8000/").status is ActionStatus.FAILED
    permitted, page2 = _adapter(permit=("127.0.0.0/8",))
    assert _navigate(permitted, "http://127.0.0.1:8000/").status is ActionStatus.SUCCESS
    assert page2.goto_calls == ["http://127.0.0.1:8000/"], "回环集成测试的注入缝失效"


def test_u925_construction_defaults_to_the_shared_env_policy(monkeypatch) -> None:
    """缺省就是大家共用的那一条 env，不另开一份"浏览器专用白名单"。"""
    monkeypatch.setenv("ATLAS_HTTP_EGRESS_ALLOWLIST", "only.example.com")
    adapter = WebHarnessAdapter(granted_permissions=_WRITE)
    page = _FakePage()
    adapter.page = page
    assert _navigate(adapter, "http://8.8.8.8/").status is ActionStatus.FAILED
    assert page.goto_calls == []


# --- U1125–U1129：route 级闸门（页面内的每个请求） ----------------------------
# 本文件不启动浏览器，所以这几条断的是**我们自己的判定与分派**；
# "真 Chromium 会不会真的拦下"由 I21/I22 验，"跳转请求根本不进回调"这条边界由 I23 钉住
# （tests/test_web_egress_gate_integration.py）。


class _FakeRoute:
    def __init__(self) -> None:
        self.continued = False
        self.aborted: str | None = None

    def continue_(self, **_kw: object) -> None:
        self.continued = True

    def abort(self, error_code: str = "failed") -> None:
        self.aborted = error_code


class _Req:
    def __init__(self, url: str) -> None:
        self.url = url


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/internal",
    "http://127.0.0.1:8000/api/graphs",
])
def test_u1125_route_guard_denies_internal_targets(url: str) -> None:
    """初始 URL 之外的目标（跳转/子资源）一到判定层就按同一把守卫判，不给"过了第一跳就自由"的口子。

    跳转请求**能不能到这一层**是平台行为，见 I23。
    """
    adapter, _page = _adapter()
    assert adapter._deny_reason(url) is not None  # noqa: SLF001


def test_u1126_route_guard_positive_control() -> None:
    """判别对照：公网目标必须判成放行，否则 U1125 可以靠"谁都拦"空绿。"""
    adapter, _page = _adapter()
    assert adapter._deny_reason("http://8.8.8.8/x.png") is None  # noqa: SLF001


@pytest.mark.parametrize("url", ["data:text/html,hi", "blob:http://a/b", "about:blank"])
def test_u1127_non_network_schemes_are_left_to_the_browser(url: str) -> None:
    """data:/blob:/about: 不出网 ⇒ 闸门不管，别把浏览器自己的内部方案判成违规。"""
    adapter, _page = _adapter()
    assert adapter._deny_reason(url) is None  # noqa: SLF001


def test_u1128_guard_route_aborts_and_records_the_denial() -> None:
    """被拦必须留下可读痕迹：静默丢弃等于没有闸门。"""
    adapter, _page = _adapter()
    route = _FakeRoute()
    adapter._guard_route(route, _Req("http://169.254.169.254/"))  # noqa: SLF001
    assert route.aborted == "failed" and not route.continued
    assert len(adapter.egress_denials) == 1
    assert "169.254.169.254" in adapter.egress_denials[0]


def test_u1129_denial_log_is_bounded() -> None:
    """无界增长会变成内存泄漏：一个页面的子资源数量由外部决定。"""
    adapter, _page = _adapter()
    for _ in range(40):
        route = _FakeRoute()
        adapter._guard_route(route, _Req("http://10.0.0.5/x"))  # noqa: SLF001
    assert len(adapter.egress_denials) == 20
