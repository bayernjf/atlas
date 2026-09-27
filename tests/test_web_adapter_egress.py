# -*- coding: utf-8 -*-
"""浏览器 `navigate` 的出向闸门（docs/32 同族；N3 复开的前置，U925）。

这批不接 `web-playwright` 进运行期注册表（那是 N3，已判为本轮非目标），但先把
"工具参数里的 url 原样交给 `page.goto()`" 这条绕过现有 SSRF 防护的通道堵上——
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
