"""三层定位单元测试（06 §6.5 / 13 文档 I1-I5 编排逻辑，不依赖真实浏览器）。"""

from __future__ import annotations

import pytest

from atlas.web.location import (
    LocatedLayer,
    ParsedElement,
    ThreeLayerLocator,
)


class FakeMouse:
    def __init__(self) -> None:
        self.clicks: list[tuple[float, float]] = []

    def click(self, x: float, y: float) -> None:
        self.clicks.append((x, y))


class FakePage:
    def __init__(self, *, valid_selectors=("#ok",)) -> None:
        self.mouse = FakeMouse()
        self._valid = set(valid_selectors)
        self.selector_clicks: list[str] = []
        self.screenshots = 0

    def click(self, selector: str, *, timeout: int) -> None:
        if selector not in self._valid:
            raise AssertionError(f"element {selector!r} not found")
        self.selector_clicks.append(selector)

    def screenshot(self) -> bytes:
        self.screenshots += 1
        return b"fake-png"


class StaticParser:
    def __init__(self, elements: list[ParsedElement]) -> None:
        self.calls = 0
        self._elements = elements

    def parse(self, screenshot: bytes) -> list[ParsedElement]:
        self.calls += 1
        return self._elements


class DescriptionMatcher:
    """按描述全等匹配的确定性桩（替代 llm_match）。"""

    def __init__(self, elements_by_desc: dict[str, ParsedElement]) -> None:
        self._by_desc = elements_by_desc

    def match(self, element_desc: str, elements: list[ParsedElement]) -> ParsedElement | None:
        return self._by_desc.get(element_desc)


class StaticVision:
    def __init__(self, coordinates: tuple[float, float] | None) -> None:
        self.calls = 0
        self._coordinates = coordinates

    def locate(self, screenshot: bytes, element_desc: str):
        self.calls += 1
        return self._coordinates


def test_layer1_selector_click_succeeds():
    page = FakePage()
    locator = ThreeLayerLocator()

    result = locator.click(page, "提交按钮", selector="#ok")

    assert result.located is True
    assert result.layer is LocatedLayer.SELECTOR
    assert page.selector_clicks == ["#ok"]
    assert page.screenshots == 0  # 层1 成功后不再截图


def test_layer2_semantic_click_after_selector_miss():
    page = FakePage(valid_selectors=())
    target = ParsedElement(120, 40, "提交按钮", selector="#submit", confidence=0.95)
    parser = StaticParser([target])
    matcher = DescriptionMatcher({"提交按钮": target})
    locator = ThreeLayerLocator(parser=parser, matcher=matcher, vision=StaticVision(None))
    cache: dict[str, str] = {}

    result = locator.click(page, "提交按钮", selector="#stale", selector_cache=cache)

    assert result.layer is LocatedLayer.SEMANTIC
    assert page.mouse.clicks == [(120, 40)]
    assert cache == {"提交按钮": "#submit"}  # I5：成功后回填选择器缓存
    assert parser.calls == 1


def test_layer2_confidence_boundary_falls_through_to_layer3():
    page = FakePage(valid_selectors=())
    target = ParsedElement(1, 1, "提交按钮", confidence=0.7)  # 阈值为 >0.7，边界不算命中
    vision = StaticVision((200, 300))
    locator = ThreeLayerLocator(
        parser=StaticParser([target]),
        matcher=DescriptionMatcher({"提交按钮": target}),
        vision=vision,
    )

    result = locator.click(page, "提交按钮")

    assert result.layer is LocatedLayer.VISION
    assert page.mouse.clicks == [(200, 300)]
    assert vision.calls == 1


def test_layer3_vision_click():
    page = FakePage(valid_selectors=())
    locator = ThreeLayerLocator(vision=StaticVision((50, 60)))

    result = locator.click(page, "关闭弹窗")

    assert result.layer is LocatedLayer.VISION
    assert page.mouse.clicks == [(50, 60)]


def test_all_layers_fail_returns_not_found():
    page = FakePage(valid_selectors=())
    locator = ThreeLayerLocator(
        parser=StaticParser([]),
        matcher=DescriptionMatcher({}),
        vision=StaticVision(None),
    )

    result = locator.click(page, "神秘按钮", selector="#missing")

    assert result.located is False
    assert result.layer is None
    assert result.message == "元素未找到，三层定位均失败"
    assert page.mouse.clicks == []


def test_selector_cache_short_circuits_to_layer1():
    page = FakePage(valid_selectors=("#cached",))
    parser = StaticParser([])  # 若进入层2会解析到空，随后兜底失败
    locator = ThreeLayerLocator(parser=parser, matcher=DescriptionMatcher({}))
    cache = {"提交按钮": "#cached"}

    result = locator.click(page, "提交按钮", selector_cache=cache)

    assert result.layer is LocatedLayer.SELECTOR
    assert page.selector_clicks == ["#cached"]
    assert parser.calls == 0
