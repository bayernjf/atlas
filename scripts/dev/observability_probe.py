#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prometheus / Grafana 本地栈验收（D11 的本地可重复证据）。

前置：Atlas 以 dev 档在 `atlas:8000` 可达（compose 项目 atlas），Prometheus 按
`deploy/prometheus/prometheus.yml` 抓取，Grafana 已挂载 `deploy/grafana` provisioning。
本脚本只读，不启动/停止容器：

    .venv/bin/python scripts/dev/observability_probe.py \
      --prom-url http://127.0.0.1:19090 \
      --grafana-url http://127.0.0.1:13001 \
      --grafana-user admin --grafana-password atlasadmin

判据：
1. Prometheus `atlas` target health=up（不是 unknown/down）；
2. `atlas_up` 查询返回 1；
3. Grafana `/api/health` database=ok；
4. `Atlas Prometheus` datasource 存在且指向 Prometheus；
5. `atlas-overview` dashboard 可读，且面板数 >0。

OTel SDK/Collector 仍按 ADR T21 / docs/14 D11 缓做；本脚本不冒充 OTel 验收。
退出码：任一条不满足非 0。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def _get(url: str, *, user: str = "", password: str = "") -> tuple[int, dict]:
    req = urllib.request.Request(url)
    if user:
        import base64

        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def main() -> int:
    ap = argparse.ArgumentParser(description="Atlas Prometheus/Grafana local stack probe")
    ap.add_argument("--prom-url", default="http://127.0.0.1:19090")
    ap.add_argument("--grafana-url", default="http://127.0.0.1:13001")
    ap.add_argument("--grafana-user", default="admin")
    ap.add_argument("--grafana-password", default="atlasadmin")
    ap.add_argument("--dashboard-uid", default="atlas-overview")
    args = ap.parse_args()

    failures: list[str] = []

    code, targets = _get(f"{args.prom_url}/api/v1/targets?state=active")
    active = (targets.get("data") or {}).get("activeTargets") or []
    atlas = [t for t in active if t.get("labels", {}).get("job") == "atlas"]
    if code != 200 or not atlas:
        failures.append(f"Prometheus target atlas 不存在（HTTP {code}）")
    else:
        health = atlas[0].get("health")
        print(f"prom target: {atlas[0].get('scrapeUrl')} health={health}")
        if health != "up":
            failures.append(f"Prometheus target atlas health={health}（期望 up）")

    code, query = _get(f"{args.prom_url}/api/v1/query?query=atlas_up")
    series = ((query.get("data") or {}).get("result") or [])
    value = (series[0].get("value") or [None, None])[1] if series else None
    print(f"prom atlas_up: {value}")
    if code != 200 or value != "1":
        failures.append(f"Prometheus 查询 atlas_up 失败（HTTP {code}, value={value}）")

    code, health = _get(f"{args.grafana_url}/api/health")
    print(f"grafana health: HTTP {code} database={health.get('database')}")
    if code != 200 or health.get("database") != "ok":
        failures.append(f"Grafana health 失败（HTTP {code}）")

    code, ds = _get(
        f"{args.grafana_url}/api/datasources/uid/atlas-prometheus",
        user=args.grafana_user, password=args.grafana_password,
    )
    print(f"grafana datasource: HTTP {code} name={ds.get('name')} type={ds.get('type')}")
    if code != 200 or ds.get("type") != "prometheus":
        failures.append(f"Grafana datasource atlas-prometheus 不可用（HTTP {code}）")

    code, dash = _get(
        f"{args.grafana_url}/api/dashboards/uid/{args.dashboard_uid}",
        user=args.grafana_user, password=args.grafana_password,
    )
    panels = (((dash.get("dashboard") or {}).get("panels")) or [])
    print(f"grafana dashboard: HTTP {code} panels={len(panels)}")
    if code != 200 or not panels:
        failures.append(f"Grafana dashboard {args.dashboard_uid} 不可读或没有面板（HTTP {code}）")

    if failures:
        for item in failures:
            print(f"FAIL {item}", file=sys.stderr)
        return 1
    print("observability probe: OK（Prometheus＋Grafana；OTel SDK/Collector 仍按 ADR T21 缓做）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
