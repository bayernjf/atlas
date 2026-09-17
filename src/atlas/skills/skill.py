"""技能与 Bot 执行体抽象（M7，08 M7 立项条 + docs/20 §4.2）。

Bot＝技能声明 + 适配器集合 + 受护栏 Coordinator（只输出意图+置信度，低置信升级，
对齐 19 §2.1：LLM 无控制流写入权、确定性 Graph 主干不动）。物流 Bot 用进程内假
物流适配器沙盘（不接真实系统），沙盘语义期不解除 D31。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Skill(BaseModel):
    """技能声明（05 §1.2 skill_schema 的子集，M7 进程内首版）。"""

    id: str
    name: str
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)  # `<adapter>/<capability>`
    required_permissions: list[str] = Field(default_factory=list)


class CoordinatorConfig(BaseModel):
    """受护栏 Coordinator：只输出意图+置信度，低于阈值升级人工（不做自由控制流）。"""

    confidence_threshold: float = 0.5


class Bot(BaseModel):
    """Bot 执行体：技能声明 + 适配器集合 + Coordinator 配置。"""

    id: str  # `bot.customer` / `bot.logistics`
    name: str
    description: str = ""
    skills: list[Skill] = Field(default_factory=list)
    adapter_ids: list[str] = Field(default_factory=list)
    coordinator: CoordinatorConfig = Field(default_factory=CoordinatorConfig)
