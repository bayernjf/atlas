"""Web 操作适配器（06 §6.5，Demo 核心）：Playwright + 三层定位。

W3-W4 基本工具集：navigate / click / type / screenshot。
浏览器经 Playwright sync API 驱动；headless 由 PLAYWRIGHT_HEADLESS 控制。
层 2/3 视觉组件为可注入依赖，模型接入前可传桩实现。
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
    ) -> None:
        super().__init__(granted_permissions=granted_permissions, audit_sink=audit_sink)
        self._locator = locator or ThreeLayerLocator()
        self._headless = (
            os.environ.get("PLAYWRIGHT_HEADLESS", "1") != "0" if headless is None else headless
        )
        self._playwright = None
        self._browser = None
        self.page: Any = None
        self._selector_cache: dict[str, str] = {}

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
