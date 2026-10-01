"""A2A 执行 Agent 面（Zeus 联邦 W2 接入，第一阶段；docs/90、ADR T32）。

与 loom Q150 同构：独立模块、不碰业务链，只暴露 plan-only 的编排域能力。
- card.py：Agent Card + x-zeus-fealty 单一事实源。
- skills.py：plan 模式 skill 执行器（纯函数，无 DB、无 LLM、无副作用）。
- rpc.py：JSON-RPC 任务生命周期（纯内存、纯函数式）。
- router.py：卡片发现（公开）+ 任务端点（Bearer 保护）。
"""

from atlas.a2a.card import CARD_VERSION, build_agent_card
from atlas.a2a.rpc import handle_jsonrpc, reset_for_tests

__all__ = ["CARD_VERSION", "build_agent_card", "handle_jsonrpc", "reset_for_tests"]
