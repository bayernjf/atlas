"""Web 三层定位（06 §6.5 / 12 §3.3）：精确选择器 → 视觉语义 → LLM 全图推理。

层 2（omni_parser 解析 + llm_match）与层 3（vision_llm）通过协议注入：
W3-W4 先用确定性/桩实现跑通编排与兜底，接入模型时替换实现，定位流程不变。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

# 层 2 置信度阈值（06 §6.5：confidence > 0.7）
SEMANTIC_CONFIDENCE_THRESHOLD = 0.7
# 层 1 选择器超时（06 §6.5：timeout=3000）
SELECTOR_TIMEOUT_MS = 3000


class LocatedLayer(str, Enum):
    SELECTOR = "selector"
    SEMANTIC = "semantic"
    VISION = "vision"


@dataclass
class ParsedElement:
    """视觉解析返回的页面元素。"""

    x: float
    y: float
    description: str
    selector: str | None = None
    confidence: float = 0.0


@dataclass
class LocationResult:
    located: bool
    layer: LocatedLayer | None = None
    message: str = ""


class ElementParser(Protocol):
    """层 2-a：截图 → 页面元素列表（落模型时为 omni-parser 类解析器）。"""

    def parse(self, screenshot: bytes) -> list[ParsedElement]: ...


class SemanticMatcher(Protocol):
    """层 2-b：元素描述与页面元素匹配（落模型时为 LLM match）。"""

    def match(self, element_desc: str, elements: list[ParsedElement]) -> ParsedElement | None: ...


class VisionLocator(Protocol):
    """层 3：全图推理直接给出坐标（落模型时为 vision LLM）。"""

    def locate(self, screenshot: bytes, element_desc: str) -> tuple[float, float] | None: ...


@dataclass
class ThreeLayerLocator:
    parser: ElementParser | None = None
    matcher: SemanticMatcher | None = None
    vision: VisionLocator | None = None

    def click(
        self,
        page: Any,
        element_desc: str,
        *,
        selector: str | None = None,
        selector_cache: dict[str, str] | None = None,
    ) -> LocationResult:
        cache = selector_cache if selector_cache is not None else {}
        effective_selector = selector or cache.get(element_desc)

        # 第一层：精确选择器（含历史成功缓存，I5）
        if effective_selector:
            try:
                page.click(effective_selector, timeout=SELECTOR_TIMEOUT_MS)
                return LocationResult(True, LocatedLayer.SELECTOR)
            except Exception:
                pass

        screenshot = page.screenshot()

        # 第二层：视觉语义定位
        if self.parser is not None and self.matcher is not None:
            elements = self.parser.parse(screenshot)
            target = self.matcher.match(element_desc, elements)
            if target is not None and target.confidence > SEMANTIC_CONFIDENCE_THRESHOLD:
                page.mouse.click(target.x, target.y)
                if target.selector:
                    cache[element_desc] = target.selector
                return LocationResult(True, LocatedLayer.SEMANTIC)

        # 第三层：LLM 全图推理
        if self.vision is not None:
            coordinates = self.vision.locate(screenshot, element_desc)
            if coordinates is not None:
                page.mouse.click(coordinates[0], coordinates[1])
                return LocationResult(True, LocatedLayer.VISION)

        return LocationResult(False, None, "元素未找到，三层定位均失败")
