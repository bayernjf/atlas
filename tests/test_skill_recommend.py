"""A-06 技能推荐：注册表投影与确定性 top-3。"""

from __future__ import annotations

from atlas.api.main import _demo_registry
from atlas.skills.recommend import build_skill_catalog, recommend_skills


def test_catalog_projects_registered_adapters():
    catalog = build_skill_catalog(_demo_registry)
    ids = {card["id"] for card in catalog}
    assert {"shop", "database", "message", "web-playwright", "memory", "http"} <= ids
    shop = next(card for card in catalog if card["id"] == "shop")
    assert "shop/execute_refund" in shop["tools"]
    assert "shop/process_refund" in shop["tools"]


def test_refund_prompt_ranks_shop_first():
    skills = recommend_skills(
        "客户申请退款，查询退款单后金额超过 1000 转人工审批，否则自动退款", _demo_registry
    )
    assert [skill["id"] for skill in skills][:1] == ["shop"]
    assert "退款" in skills[0]["matched"]


def test_notify_prompt_hits_message_top3():
    skills = recommend_skills("退款成功后给客户发邮件通知", _demo_registry)
    assert "message" in {skill["id"] for skill in skills}


def test_sql_prompt_hits_database_top3():
    skills = recommend_skills("用 SQL 统计上个月退款金额并生成报表", _demo_registry)
    assert "database" in {skill["id"] for skill in skills}


def test_browser_prompt_hits_web_top3():
    skills = recommend_skills("打开网页登录商家后台，点击退款按钮并截图", _demo_registry)
    assert "web-playwright" in {skill["id"] for skill in skills}


def test_memory_prompt_hits_memory_top3():
    skills = recommend_skills("记住客户偏好，以后不要电话联系", _demo_registry)
    assert "memory" in {skill["id"] for skill in skills}


def test_http_prompt_hits_http_top3():
    skills = recommend_skills("调用 ERP 的 HTTP 接口查询库存", _demo_registry)
    assert "http" in {skill["id"] for skill in skills}


def test_same_prompt_is_stable_and_respects_top_k():
    prompt = "退款后发邮件通知"
    first = recommend_skills(prompt, _demo_registry, top_k=3)
    second = recommend_skills(prompt, _demo_registry, top_k=3)
    assert first == second
    assert len(recommend_skills(prompt, _demo_registry, top_k=0)) == 0
