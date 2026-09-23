"""graph/diff.py 纯函数测试（B3，D26 跨版本配置差异子集）。"""

from atlas.graph.diff import diff_graph, diff_summary, has_changes


def _node(id, type="tool", name=None, config=None):
    return {
        "id": id,
        "type": type,
        "name": name or id,
        "position": {"x": 0, "y": 0},
        "config": config or {},
    }


def _edge(id, source, target):
    return {"id": id, "source": source, "target": target}


def _var(name, value, type="string"):
    return {"name": name, "type": type, "value": value, "scope": "global"}


def test_empty_to_graph_marks_everything_added():
    cand = {
        "nodes": [_node("a"), _node("b")],
        "edges": [_edge("e1", "a", "b")],
        "variables": [_var("v", 1, "int")],
    }
    diff = diff_graph({}, cand)
    assert diff["nodes"]["added"] == ["a", "b"]
    assert diff["edges"]["added"] == ["e1"]
    assert diff["variables"]["added"] == ["v"]
    assert has_changes(diff) is True


def test_nodes_added_removed_and_changed():
    base = {
        "nodes": [
            _node("keep", config={"url": "x", "n": 1}),
            _node("gone"),
            _node("typed", type="tool", name="Old"),
        ]
    }
    cand = {
        "nodes": [
            _node("keep", config={"url": "x", "n": 2, "extra": True}),
            _node("new"),
            _node("typed", type="condition", name="New"),
        ]
    }
    diff = diff_graph(base, cand)
    assert diff["nodes"]["added"] == ["new"]
    assert diff["nodes"]["removed"] == ["gone"]
    changed = {c["id"]: c["changes"] for c in diff["nodes"]["changed"]}
    assert set(changed) == {"keep", "typed"}
    keep_fields = {ch["field"]: ch for ch in changed["keep"]}
    assert keep_fields["config"]["configKeys"] == ["extra", "n"]
    typed_fields = {ch["field"]: ch for ch in changed["typed"]}
    assert typed_fields["type"]["from"] == "tool"
    assert typed_fields["type"]["to"] == "condition"
    assert typed_fields["name"]["to"] == "New"


def test_edges_added_and_removed():
    base = {"edges": [_edge("e1", "a", "b"), _edge("e2", "b", "c")]}
    cand = {"edges": [_edge("e2", "b", "c"), _edge("e3", "c", "d")]}
    diff = diff_graph(base, cand)
    assert diff["edges"] == {"added": ["e3"], "removed": ["e1"]}


def test_variables_added_removed_and_changed():
    base = {
        "variables": [_var("keep", 1, "int"), _var("gone", "x"), _var("same", "s")]
    }
    cand = {
        "variables": [_var("keep", 2, "int"), _var("new", "y"), _var("same", "s")]
    }
    diff = diff_graph(base, cand)
    assert diff["variables"]["added"] == ["new"]
    assert diff["variables"]["removed"] == ["gone"]
    assert diff["variables"]["changed"] == ["keep"]


def test_identical_graph_has_no_changes():
    raw = {
        "nodes": [_node("a", config={"k": "v"})],
        "edges": [_edge("e1", "a", "a")],
        "variables": [_var("v", 1)],
    }
    diff = diff_graph(raw, dict(raw))
    assert has_changes(diff) is False
    assert set(diff_summary(diff).values()) == {0}


def test_position_change_alone_is_not_a_change():
    base = {"nodes": [_node("a")]}
    cand = {"nodes": [{**_node("a"), "position": {"x": 100, "y": 200}}]}
    diff = diff_graph(base, cand)
    assert diff["nodes"]["changed"] == []
    assert has_changes(diff) is False
