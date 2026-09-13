"""Web 适配器浏览器集成测试（13 文档 I1/I2/I5，真实 Chromium）。

默认跳过；需要本机已安装 Playwright Chromium：
    ATLAS_RUN_INTEGRATION=1 .venv/bin/pytest -m integration
（不依赖 DATABASE_URL；浏览器缺失时自动 skip）
"""

from __future__ import annotations

import os

import pytest

from atlas.harness.base import ActionRequest, ActionStatus, Permission
from atlas.web.adapter import WebHarnessAdapter
from atlas.web.location import ParsedElement, ThreeLayerLocator

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ATLAS_RUN_INTEGRATION") != "1",
        reason="set ATLAS_RUN_INTEGRATION=1 to run browser integration tests",
    ),
]

TEST_HTML = """
<!doctype html><html><head><title>harness-smoke</title></head>
<body>
  <button id="btn" onclick="window.n=(window.n||0)+1;document.getElementById('v').textContent=window.n">提交按钮</button>
  <span id="v">0</span>
  <input id="name" />
</body></html>
"""


class _Parser:
    """按元素真实包围盒产出 ParsedElement 的确定性桩（替代 omni-parser）。"""

    def __init__(self, page, selector: str, description: str, confidence: float = 0.95):
        self.page = page
        self.selector = selector
        self.description = description
        self.confidence = confidence

    def parse(self, screenshot: bytes):
        box = self.page.locator(self.selector).bounding_box()
        return [
            ParsedElement(
                box["x"] + box["width"] / 2,
                box["y"] + box["height"] / 2,
                self.description,
                selector=self.selector,
                confidence=self.confidence,
            )
        ]


class _Matcher:
    def __init__(self, description: str):
        self.description = description

    def match(self, element_desc: str, elements: list[ParsedElement]):
        return next((e for e in elements if e.description == element_desc), None)


@pytest.fixture(scope="module")
def adapter():
    try:
        web = WebHarnessAdapter(
            granted_permissions={Permission.READ, Permission.WRITE}, headless=True
        )
        web.start()
    except Exception as exc:  # 浏览器未安装/无法启动
        pytest.skip(f"chromium unavailable: {exc}")
    web.page.set_content(TEST_HTML)
    yield web
    web.close()


def _counter(page) -> int:
    return int(page.locator("#v").inner_text())


def test_i1_layer1_selector_click(adapter):
    assert _counter(adapter.page) == 0
    result = adapter.execute(ActionRequest("click", parameters={"element_desc": "提交按钮", "selector": "#btn"}))
    assert result.status is ActionStatus.SUCCESS
    assert result.output["layer"] == "selector"
    assert _counter(adapter.page) == 1


def test_i2_layer2_semantic_click_real_coordinates(adapter):
    adapter._locator = ThreeLayerLocator(
        parser=_Parser(adapter.page, "#btn", "提交按钮"),
        matcher=_Matcher("提交按钮"),
    )
    result = adapter.execute(ActionRequest("click", parameters={"element_desc": "提交按钮"}))
    assert result.status is ActionStatus.SUCCESS
    assert result.output["layer"] == "semantic"
    assert _counter(adapter.page) == 2
    # I5：层2成功后选择器已入缓存
    assert adapter._selector_cache["提交按钮"] == "#btn"


def test_i5_cached_selector_short_circuits_next_click(adapter):
    result = adapter.execute(ActionRequest("click", parameters={"element_desc": "提交按钮"}))
    assert result.status is ActionStatus.SUCCESS
    assert result.output["layer"] == "selector"
    assert _counter(adapter.page) == 3


def test_type_and_observe(adapter):
    result = adapter.execute(
        ActionRequest("type", parameters={"selector": "#name", "text": "atlas"})
    )
    assert result.status is ActionStatus.SUCCESS
    assert adapter.page.locator("#name").input_value() == "atlas"
    observation = adapter.observe()
    assert observation.title == "harness-smoke"
