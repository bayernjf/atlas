"""W1 冒烟测试：OODA 最小循环能按 max_steps 收敛到 completed。"""

from atlas.engine.loop import build_graph, initial_state
from atlas.engine.state import LoopState


def test_loop_reaches_completed():
    graph = build_graph()
    final: LoopState = graph.invoke(initial_state("demo goal", max_steps=2))

    assert final["status"] == "completed"
    assert final["current_node"] == "reflect"
    assert final["variables"]["step"] == 2
    assert len(final["observations"]) == 2
    assert final["goal"] == "demo goal"


def test_loop_default_three_steps():
    final = build_graph().invoke(initial_state("goal"))
    assert final["variables"]["step"] == 3
    assert final["status"] == "completed"


def test_history_contains_each_phase():
    final = build_graph().invoke(initial_state("goal", max_steps=1))
    joined = "\n".join(final["messages"])
    for phase in ("orient", "decide", "reflect"):
        assert phase in joined
    # max_steps=1：decide 直接判定完成，act 不执行
    assert not any(m.startswith("act@") for m in final["messages"])
