"""技能与 Bot 执行体抽象（M7，多 Bot 任务总线；08 M7 立项条）。"""

from .skill import Bot, CoordinatorConfig, Skill

__all__ = ["Bot", "CoordinatorConfig", "Skill"]
