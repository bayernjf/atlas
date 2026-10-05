# -*- coding: utf-8 -*-
"""打包 AY（docs/73 4.1 的凭据脚手架）：真外发腿"跑不跑"必须是**点名可执行**的判断。

演练脚本第 9 段的规划层是纯函数，本文件因此完全不碰 docker、不读真凭据、不发外发请求；
它管的是三件会静默坏掉的事：
  ① 缺凭据时必须点名缺哪个变量（沉默跳过＝下一次以为这条腿演过了）；
  ② 动款／打扰真人的腿不能被"凭据齐了"自动放行；
  ③ 规划层的任何输出都不许带凭据值（打印出来就等于泄漏）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "dev" / "prod_rehearsal.py"

_spec = importlib.util.spec_from_file_location("prod_rehearsal", SCRIPT)
assert _spec is not None and _spec.loader is not None
rh = importlib.util.module_from_spec(_spec)
sys.modules["prod_rehearsal"] = rh
_spec.loader.exec_module(rh)

LLM_OK = {"LITELLM_MODEL": "openai/some-model", "OPENAI_API_KEY": "sk-any", "OPENAI_BASE_URL": "https://x/v1"}
SMTP_OK = {"ATLAS_SMTP_HOST": "smtp.example.com", "ATLAS_SMTP_USERNAME": "u", "ATLAS_SMTP_FROM": "a@b.c"}
BASE_ENV = {"ATLAS_MASTER_KEY": "base-master-key", "LITELLM_MODEL": "", "OPENAI_API_KEY": "", "OPENAI_BASE_URL": ""}


def verdict(plans, name):
    got = [p for p in plans if p.name == name]
    assert len(got) == 1, plans
    return got[0]


def test_u1156_missing_llm_credential_is_named_not_silently_skipped():
    plan = verdict(rh.plan_live_legs(["llm"], {}), "llm")
    assert plan.verdict == "skip"
    assert "LITELLM_MODEL" in plan.reason and "OPENAI_API_KEY" in plan.reason
    # 判据本身也要跟着输出，否则 SKIP 只是一句"没跑"
    assert "source" in plan.reason

    # 变量名陷阱是这条腿的真历史：LITELLM_API_KEY 在直连路径上不被读取
    assert "LITELLM_API_KEY" in plan.reason


def test_u1156_partial_credential_names_only_the_missing_one():
    plan = verdict(rh.plan_live_legs(["llm"], {"LITELLM_MODEL": "", "OPENAI_API_KEY": "sk-x"}), "llm")
    assert plan.verdict == "skip"
    assert "LITELLM_MODEL" in plan.reason
    assert "缺前置 OPENAI_API_KEY" not in plan.reason


def test_u1156_credentials_present_then_the_leg_is_runnable():
    plan = verdict(rh.plan_live_legs(["llm"], LLM_OK), "llm")
    assert plan.verdict == "run"
    assert plan.reason.startswith("prod 下")


def test_u1156_unknown_leg_is_an_error_not_an_ignored_word():
    with pytest.raises(ValueError, match="未知外发腿"):
        rh.plan_live_legs(["shopfi"], LLM_OK)


def test_u1156_money_and_humans_need_explicit_authorisation():
    denied = verdict(rh.plan_live_legs(["refund"], LLM_OK), "refund")
    assert denied.verdict == "blocked" and "--allow-refund" in denied.reason

    no_order = verdict(
        rh.plan_live_legs(["refund"], LLM_OK, allow_refund=True), "refund"
    )
    assert no_order.verdict == "blocked" and "--shopify-order" in no_order.reason

    authorised = verdict(
        rh.plan_live_legs(["refund"], LLM_OK, allow_refund=True, shopify_order_id="1234"), "refund"
    )
    assert authorised.verdict == "blocked"
    # 授权齐了也不能假装跑过：没有执行段就是没有
    assert "执行段未实现" in authorised.reason


def test_u1156_notify_leg_reports_the_relay_gap_it_found():
    no_creds = verdict(rh.plan_live_legs(["notify"], {}), "notify")
    assert no_creds.verdict == "skip"
    assert "ATLAS_SMTP_HOST" in no_creds.reason
    # 实测过的部署面缺口：compose 只透传 LITELLM_*/OPENAI_*，SMTP 根本没进容器
    assert "docker-compose.yml" in no_creds.reason

    needs_recipient = verdict(rh.plan_live_legs(["notify"], SMTP_OK), "notify")
    assert needs_recipient.verdict == "blocked" and "--notify-to" in needs_recipient.reason


def test_u1156_only_legs_with_an_executor_can_ever_run():
    plans = rh.plan_live_legs(["llm", "shopify", "refund", "notify"],
                              {**LLM_OK, **SMTP_OK},
                              allow_refund=True, shopify_order_id="1", notify_to="ops@example.com")
    running = {p.name for p in plans if p.verdict == "run"}
    assert running == set(rh.EXECUTABLE_LEGS)


def test_u1156_planner_never_echoes_a_credential_value():
    sentinel = "sk-SECRET-DO-NOT-PRINT"
    plans = rh.plan_live_legs(["llm", "notify"], {"LITELLM_MODEL": sentinel, "OPENAI_API_KEY": ""})
    for plan in plans:
        assert sentinel not in plan.reason
        assert sentinel not in plan.verdict


def test_u1156_credential_copy_is_whitelisted_and_silent():
    merged, copied = rh.with_live_credentials(
        BASE_ENV,
        {"LITELLM_MODEL": "m", "OPENAI_API_KEY": "k", "ATLAS_MASTER_KEY": "should-not-override"},
    )
    assert sorted(copied) == ["LITELLM_MODEL", "OPENAI_API_KEY"]
    assert merged["ATLAS_MASTER_KEY"] == "base-master-key"
    # 没给的键保持 base 的空值＝默认路径的凭据边界不变
    assert merged["OPENAI_BASE_URL"] == ""
    assert "should-not-override" not in merged.values()
