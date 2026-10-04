"""Web 操作适配器（06 §6.5，Demo 核心）：Playwright + 三层定位。

W3-W4 基本工具集：navigate / click / type / screenshot。
浏览器经 Playwright sync API 驱动；headless 由 PLAYWRIGHT_HEADLESS 控制。
层 2/3 视觉组件为可注入依赖，模型接入前可传桩实现。

出向治理分两层（docs/89 §14 N-1）：
1. `navigate` 的 url 先过 `EgressGuard`（与 httpapi 同一份策略与 env
   `ATLAS_HTTP_EGRESS_ALLOWLIST`），失败返回 `EGRESS_DENIED`／`EGRESS_INVALID_URL` 且**不碰浏览器**；
2. 页面内**每一个由我们发起的**网络请求（iframe、img/script/XHR 等子资源）在 `page.route()` 上再过
   一次同一份守卫。被拦的请求直接 `abort`，原因记在 `egress_denials`（有界），运行输出里看得见，
   不是静默跳过。非 http/https 的 scheme（`data:`／`blob:` 等）由浏览器本地解析、不出网，交回浏览器。

**这一层挡不住 3xx 跳转**：Playwright 的路由不给跳转后的请求回调（driver 里
`_onRequestWillBeSent` 对带 `redirectResponse` 的事件直接放行、不进拦截），实测见 I23 ⇒
「一次 302 把浏览器带进内网/云元数据」这条绕过路径**没有**在这里被收掉。收它的网络层做法是
egress 代理（`--proxy-server` 逐跳判定），登记为 docs/14 D52；在它落地之前，真浏览器**不进 prod
运行期注册表**（`harness/runtime.py` 与 shop/database 同一条 `demo_surface_enabled()` 判定）。
"""

from __future__ import annotations

import os
from typing import Any

from atlas.harness.base import (
    ActionRequest,
    ActionResult,
    Capability,
    HarnessAdapter,
    Observation,
    Permission,
    StructuredError,
)
from atlas.security.egress import EgressDenied, EgressGuard

from .location import ThreeLayerLocator


def _capability(
    name: str,
    description: str,
    *,
    permission: Permission = Permission.READ,
    is_idempotent: bool = False,
) -> Capability:
    return Capability(
        name=name,
        description=description,
        action=name,
        permission=permission,
        is_idempotent=is_idempotent,
    )


class WebHarnessAdapter(HarnessAdapter):
    adapter_id = "web-playwright"
    adapter_type = "web"

    def __init__(
        self,
        *,
        locator: ThreeLayerLocator | None = None,
        granted_permissions: set[Permission] | None = None,
        audit_sink=None,
        headless: bool | None = None,
        egress: EgressGuard | None = None,
    ) -> None:
        super().__init__(granted_permissions=granted_permissions, audit_sink=audit_sink)
        self._locator = locator or ThreeLayerLocator()
        self._headless = (
            os.environ.get("PLAYWRIGHT_HEADLESS", "1") != "0" if headless is None else headless
        )
        # 一条出向策略，不复用两份配置：浏览器与 httpx 走的是同一个"能不能出去"判断，
        # 分叉的 knob 只会让人忘记配其中一个（docs/32 的 denylist 同族）。
        self._egress = egress if egress is not None else EgressGuard.from_env()
        # 被 route 级闸门拦下的请求（有界，供运行输出与排障读）；静默丢弃等于没闸门。
        self.egress_denials: list[str] = []
        self._playwright = None
        self._browser = None
        self.page: Any = None
        self._selector_cache: dict[str, str] = {}

    # 出向判定 ---------------------------------------------------------
    def _deny_reason(self, url: str) -> str | None:
        """页面内单个请求的出向判定：返回拦截原因，`None` 表示放行。

        非 http/https（`data:`／`blob:`／`about:`…）不出网，交回浏览器；这里不扩成
        第二份策略，判定权仍在同一把 `EgressGuard`。
        """
        scheme = url.split(":", 1)[0].lower() if ":" in url else ""
        if scheme not in ("http", "https"):
            return None
        try:
            self._egress.check(url)
        except EgressDenied as exc:
            return str(exc)
        return None

    def _guard_route(self, route: Any, request: Any) -> None:
        """`page.route("**/*")` 的处理器：页面内每个请求过同一份守卫（跳转目标除外，见模块 docstring）。"""
        url = getattr(request, "url", "") or ""
        reason = self._deny_reason(url)
        if reason is None:
            route.continue_()
            return
        if len(self.egress_denials) < 20:
            self.egress_denials.append(f"{url} → {reason}")
        route.abort("failed")

    def list_capabilities(self) -> list[Capability]:
        return [
            _capability("navigate", "打开指定 URL", permission=Permission.WRITE),
            _capability("click", "按描述或选择器点击页面元素", permission=Permission.WRITE),
            _capability("type", "在输入框中填写文本", permission=Permission.WRITE),
            _capability("screenshot", "对当前页面截图", is_idempotent=True),
        ]

    # 浏览器生命周期 -------------------------------------------------
    def start(self) -> None:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        self.page = self._browser.new_page()
        # 逐请求闸门：初始 URL 之外的子资源走同一份守卫（跳转目标挡不住，见模块 docstring 与 I23）。
        self.page.route("**/*", self._guard_route)

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._browser = None
        self._playwright = None
        self.page = None

    def __enter__(self) -> "WebHarnessAdapter":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # 观测 -----------------------------------------------------------
    def observe(self) -> Observation:
        if self.page is None:
            return Observation()
        return Observation(url=self.page.url, title=self.page.title())

    # 执行 -----------------------------------------------------------
    def _execute(self, request: ActionRequest) -> ActionResult:
        if self.page is None:
            return ActionResult.failed(
                StructuredError("BROWSER_NOT_STARTED", "call start() before execute()")
            )
        handler = {
            "navigate": self._do_navigate,
            "click": self._do_click,
            "type": self._do_type,
            "screenshot": self._do_screenshot,
        }.get(request.capability_name)
        if handler is None:
            return ActionResult.failed(
                StructuredError("UNKNOWN_CAPABILITY", request.capability_name)
            )
        return handler(request)

    def _do_navigate(self, request: ActionRequest) -> ActionResult:
        url = request.parameters.get("url")
        if not url:
            return ActionResult.failed(StructuredError("MISSING_PARAMETER", "url is required"))
        # 先闸门后浏览器：工具参数里的 url 直接交给 page.goto()，等于在 httpx 那套
        # SSRF denylist 之外新开一条出向通道（内网/云元数据同形可达）。docs/32。
        try:
            self._egress.check(url)
        except EgressDenied as exc:
            return ActionResult.failed(StructuredError(exc.code, str(exc)))
        timeout = request.timeout or 30000
        self.page.goto(url, timeout=int(timeout * 1000))
        return ActionResult.success({"url": self.page.url})

    def _do_click(self, request: ActionRequest) -> ActionResult:
        element_desc = request.parameters.get("element_desc")
        if not element_desc:
            return ActionResult.failed(
                StructuredError("MISSING_PARAMETER", "element_desc is required")
            )
        result = self._locator.click(
            self.page,
            element_desc,
            selector=request.parameters.get("selector"),
            selector_cache=self._selector_cache,
        )
        if not result.located:
            return ActionResult.failed(StructuredError("ELEMENT_NOT_FOUND", result.message))
        return ActionResult.success({"layer": result.layer.value})

    def _do_type(self, request: ActionRequest) -> ActionResult:
        selector = request.parameters.get("selector")
        text = request.parameters.get("text", "")
        if not selector:
            return ActionResult.failed(
                StructuredError("MISSING_PARAMETER", "selector is required for type")
            )
        self.page.fill(selector, text, timeout=3000)
        return ActionResult.success({"selector": selector, "length": len(text)})

    def _do_screenshot(self, request: ActionRequest) -> ActionResult:
        png = self.page.screenshot()
        return ActionResult.success({"bytes": len(png)}, screenshots=[png])
