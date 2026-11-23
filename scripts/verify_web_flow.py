#!/usr/bin/env python3
"""A-01：用 Atlas 真浏览器适配器跑一条本机真实站点流程并留证据。

目标应用：Loom 前端（默认 http://127.0.0.1:3000/zh-CN/analytics）。
流程：navigate → read → 一次可见失败的 click → type 两个日期 → 重试 click「应用」→ read 过滤结果 → 截图。
证据：outputs/a01-web-flow/ 下的 trace.json 与两张截图。

默认只跑本地回环；ATLAS_ENV=prod 时回环放行被强制关闭（且浏览器适配器本就不注册）。
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from atlas.harness.base import ActionRequest, ActionStatus, Permission
from atlas.web.adapter import WebHarnessAdapter


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status(result: Any) -> str:
    value = getattr(result.status, "value", result.status)
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:3000/zh-CN/analytics")
    parser.add_argument("--out", default="outputs/a01-web-flow")
    parser.add_argument("--date-from", default="2026-11-01")
    parser.add_argument("--date-to", default="2026-11-23")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    trace: dict[str, Any] = {
        "goal": "A-01 local web flow",
        "target": args.url,
        "atlas_env": os.getenv("ATLAS_ENV", "dev"),
        "started_at": _now(),
        "steps": [],
    }

    adapter = WebHarnessAdapter(
        granted_permissions={Permission.READ, Permission.WRITE}, headless=True
    )
    adapter.start()

    def step(name: str, capability: str, params: dict[str, Any], *, screenshot: str | None = None):
        started = time.monotonic()
        result = adapter.execute(ActionRequest(capability, parameters=params))
        record = {
            "name": name,
            "capability": capability,
            "parameters": params,
            "status": _status(result),
            "error": (
                None
                if result.error is None
                else {"code": result.error.code, "message": result.error.message}
            ),
            "output": result.output,
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
            "at": _now(),
        }
        if screenshot:
            adapter.page.screenshot(path=str(out_dir / screenshot))
            record["screenshot"] = screenshot
        if adapter.page is not None:
            record["observation"] = {
                "url": adapter.page.url,
                "title": adapter.page.title(),
            }
        trace["steps"].append(record)
        return result

    try:
        nav = step("navigate-loom-analytics", "navigate", {"url": args.url}, screenshot="01-loaded.png")
        if nav.status is not ActionStatus.SUCCESS:
            raise RuntimeError(f"navigate failed: {nav.error}")

        page_text = adapter.page.locator("body").inner_text()
        trace["read_after_load"] = {
            "title": adapter.page.title(),
            "url": adapter.page.url,
            "has_analytics_heading": "数据分析" in page_text,
            "has_effect_count": "效果记录数" in page_text,
        }

        # 失败可见：故意点一个不存在的选择器；适配器必须返回 ELEMENT_NOT_FOUND，不静默。
        failed = step(
            "click-missing-selector-expected-failure",
            "click",
            {"element_desc": "不存在的按钮", "selector": "#a01-missing-button"},
        )
        trace["expected_failure"] = {
            "status": _status(failed),
            "code": None if failed.error is None else failed.error.code,
            "visible_in_trace": True,
        }
        if failed.error is None or failed.error.code != "ELEMENT_NOT_FOUND":
            trace["trace_path"] = "trace.json"
            (out_dir / "trace.json").write_text(
                json.dumps(trace, ensure_ascii=False, indent=2)
            )
            raise RuntimeError("expected a visible ELEMENT_NOT_FOUND, got none")

        type_from = step(
            "type-date-from",
            "type",
            {"selector": "input[name=date_from]", "text": args.date_from},
        )
        if type_from.status is not ActionStatus.SUCCESS:
            raise RuntimeError(f"type date_from failed: {type_from.error}")
        type_to = step(
            "type-date-to",
            "type",
            {"selector": "input[name=date_to]", "text": args.date_to},
        )
        if type_to.status is not ActionStatus.SUCCESS:
            raise RuntimeError(f"type date_to failed: {type_to.error}")

        # 重试：同一个动作换可解析的选择器后成功（失败不是静默，也不是死路）。
        applied = step(
            "click-apply-retry",
            "click",
            {"element_desc": "应用", "selector": "button[type=submit]"},
        )
        if applied.status is not ActionStatus.SUCCESS:
            raise RuntimeError(f"apply click failed: {applied.error}")

        adapter.page.wait_for_timeout(1500)
        final_text = adapter.page.locator("body").inner_text()
        trace["read_after_filter"] = {
            "url": adapter.page.url,
            "query_applied": f"date_from={args.date_from}" in adapter.page.url
            and f"date_to={args.date_to}" in adapter.page.url,
            "effect_count_visible": "效果记录数" in final_text,
            "body_excerpt": final_text[:400],
        }
        step("final-read", "screenshot", {}, screenshot="02-filtered.png")
        trace["egress_denials"] = adapter.egress_denials

        ok = bool(trace["read_after_filter"]["query_applied"])
        trace["ok"] = ok
    except Exception as exc:  # noqa: BLE001 - 证据脚本需要把失败原样落盘
        trace["ok"] = False
        trace["fatal_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        adapter.close()
        trace["ended_at"] = _now()
        (out_dir / "trace.json").write_text(
            json.dumps(trace, ensure_ascii=False, indent=2)
        )

    print(json.dumps(
        {
            "ok": trace.get("ok"),
            "trace": str(out_dir / "trace.json"),
            "steps": len(trace.get("steps", [])),
            "expected_failure": trace.get("expected_failure"),
            "query_applied": trace.get("read_after_filter", {}).get("query_applied"),
            "fatal_error": trace.get("fatal_error"),
        },
        ensure_ascii=False,
    ))
    return 0 if trace.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
