"""内置流程模板目录不变量测试（04 §5.10；13 U27）。"""

from __future__ import annotations

import re

from atlas.graph.dsl import parse_graph, validate_graph
from atlas.llm.nl_generate import generate_graph
from atlas.template import TEMPLATES, get_template, list_templates
from atlas.template.graphs import refund_template_graph

# demo 注册表（build_demo_registry）中的全部适配器工具名
DEMO_TOOLS = {
    "shop/login",
    "shop/list_pending_refunds",
    "shop/execute_refund",
    "shop/request_human_approval",
    "shop/process_refund",
    "http/request",
    "database/query",
    "database/execute",
    "message/send",
}

EXPECTED_TEMPLATE_IDS = {
    "refund-auto",
    "http-orders-branch",
    "sql-query-notify",
    "sql-approval-write",
    "approval-timeout-reject",
}


def test_catalog_has_five_unique_kebab_ids():
    ids = [template.id for template in TEMPLATES]
    assert len(ids) == 5
    assert set(ids) == EXPECTED_TEMPLATE_IDS
    assert len(set(ids)) == len(ids)
    for template_id in ids:
        assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", template_id)


def test_catalog_accessors():
    assert [template.id for template in list_templates()] == [
        template.id for template in TEMPLATES
    ]
    assert get_template("refund-auto") is TEMPLATES[0]
    assert get_template("nope") is None


def test_metadata_is_populated():
    for template in TEMPLATES:
        assert template.name.strip()
        assert template.description.strip()
        assert template.tags and all(tag.strip() for tag in template.tags)
        assert template.graph["nodes"]


def test_every_template_graph_passes_full_dsl_validation():
    for template in TEMPLATES:
        parsed = parse_graph(template.graph)
        assert validate_graph(parsed) == [], template.id


def test_template_tools_subset_of_demo_registry():
    for template in TEMPLATES:
        tools = {
            node["config"]["tool"]
            for node in template.graph["nodes"]
            if node["type"] == "tool_call"
        }
        assert tools, template.id
        assert tools <= DEMO_TOOLS, (template.id, tools - DEMO_TOOLS)


def test_refund_template_is_single_source_for_nl_fallback():
    assert generate_graph("帮我做一个电商退款自动审批流程") == refund_template_graph()
    assert get_template("refund-auto").graph == refund_template_graph()
