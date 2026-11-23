"""技能推荐（题二 B1 步骤 2：retrieve/rank 选原子）。

技能卡以适配器为粒度（05 §1.6：一个技能可以包含多条 capability），
候选只来自当前租户可见的 AdapterRegistry，不靠 LLM 凭空捏造。
排序是确定性的关键词/能力重叠打分：同一输入在同一注册表上稳定返回同一 top-k。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atlas.harness.registry import AdapterRegistry


@dataclass(frozen=True)
class _SkillMeta:
    id: str
    name: str
    description: str
    keywords: tuple[str, ...]
    priority: int


# 策展元数据：只做召回提示，不改变注册表里的工具事实。
# 关键词同时覆盖中英文，避免中文需求只能靠英文工具名碰运气。
_SKILL_META: tuple[_SkillMeta, ...] = (
    _SkillMeta(
        id="shop",
        name="售后退款",
        description="退款单查询、退款/拒绝、人工审批与资金操作",
        keywords=("退款", "退货", "售后", "退款单", "订单", "资金", "审批", "refund", "shopify"),
        priority=60,
    ),
    _SkillMeta(
        id="message",
        name="消息通知",
        description="邮件、Webhook、钉钉、企微、飞书等消息投递",
        keywords=("通知", "消息", "邮件", "短信", "钉钉", "企微", "飞书", "webhook", "email", "notify"),
        priority=50,
    ),
    _SkillMeta(
        id="database",
        name="数据查询",
        description="只读 SQL 查询与写操作，适合统计、汇总和报表",
        keywords=("数据库", "sql", "查询", "统计", "报表", "汇总", "金额", "database"),
        priority=40,
    ),
    _SkillMeta(
        id="web-playwright",
        name="网页操作",
        description="浏览器打开页面、点击、填写与截图",
        keywords=("网页", "页面", "浏览器", "点击", "填写", "截图", "登录后台", "browser", "click", "screenshot"),
        priority=30,
    ),
    _SkillMeta(
        id="memory",
        name="长期记忆",
        description="记住事实/偏好并做语义检索",
        keywords=("记忆", "偏好", "记住", "知识库", "检索", "客户历史", "recall", "memory", "preference"),
        priority=20,
    ),
    _SkillMeta(
        id="http",
        name="HTTP 接口",
        description="调用任意 HTTP API，适合 ERP、库存等外部系统",
        keywords=("http", "api", "接口", "请求", "erp", "库存", "request"),
        priority=10,
    ),
)


def _adapter_tools(adapter: dict[str, Any]) -> list[str]:
    adapter_id = str(adapter.get("id") or "")
    if not adapter_id:
        return []
    names: list[str] = []
    for tool in adapter.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if name:
            names.append(f"{adapter_id}/{name}")
    return names


def _fallback_meta(adapter: dict[str, Any]) -> _SkillMeta:
    adapter_id = str(adapter.get("id") or "unknown")
    tool_names = [str(t.get("name") or "") for t in adapter.get("tools") or [] if isinstance(t, dict)]
    return _SkillMeta(
        id=adapter_id,
        name=str(adapter.get("type") or adapter_id),
        description="、".join(name for name in tool_names if name) or adapter_id,
        keywords=(adapter_id,),
        priority=0,
    )


def build_skill_catalog(registry: AdapterRegistry) -> list[dict[str, Any]]:
    """把注册表投影成技能卡；未登记的适配器用 id 兜底，不伪造能力。"""
    adapters = {str(item.get("id")): item for item in registry.list_adapters() if item.get("id")}
    cards: list[dict[str, Any]] = []
    for meta in _SKILL_META:
        adapter = adapters.get(meta.id)
        if adapter is None:
            continue
        tools = _adapter_tools(adapter)
        if not tools:
            continue
        cards.append(
            {
                "id": meta.id,
                "name": meta.name,
                "description": meta.description,
                "keywords": list(meta.keywords),
                "tools": tools,
                "priority": meta.priority,
            }
        )
    known = {card["id"] for card in cards}
    for adapter_id, adapter in sorted(adapters.items()):
        if adapter_id in known:
            continue
        meta = _fallback_meta(adapter)
        tools = _adapter_tools(adapter)
        if not tools:
            continue
        cards.append(
            {
                "id": meta.id,
                "name": meta.name,
                "description": meta.description,
                "keywords": list(meta.keywords),
                "tools": tools,
                "priority": meta.priority,
            }
        )
    return cards


def _score_card(prompt: str, card: dict[str, Any]) -> tuple[float, list[str]]:
    prompt_lower = prompt.casefold()
    score = 0.0
    matched: list[str] = []
    for keyword in card.get("keywords") or []:
        needle = str(keyword).casefold()
        if not needle:
            continue
        if needle in prompt_lower:
            # 长关键词更具体，权重更高；只做确定性子串匹配。
            score += 2.0 + min(len(needle), 8) * 0.5
            matched.append(str(keyword))
    for tool in card.get("tools") or []:
        tail = str(tool).split("/", 1)[-1]
        for token in tail.replace("-", "_").split("_"):
            if len(token) >= 3 and token.casefold() in prompt_lower:
                score += 0.5
                matched.append(token)
    return score, matched


def recommend_skills(
    prompt: str, registry: AdapterRegistry, *, top_k: int = 3
) -> list[dict[str, Any]]:
    """返回 top-k 技能卡；分数与命中词一并给出，便于运营理解为什么推荐。"""
    if top_k <= 0:
        return []
    cards = build_skill_catalog(registry)
    ranked: list[dict[str, Any]] = []
    for card in cards:
        score, matched = _score_card(prompt, card)
        ranked.append(
            {
                "id": card["id"],
                "name": card["name"],
                "description": card["description"],
                "score": round(score, 3),
                "matched": matched,
                "tools": card["tools"],
                "priority": card["priority"],
            }
        )
    ranked.sort(key=lambda item: (-item["score"], -item["priority"], item["id"]))
    return ranked[:top_k]
