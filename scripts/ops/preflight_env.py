#!/usr/bin/env python3
"""生产入口 env 预检：缺哪把密钥就点名、说清用途与后果（docs/73 W4 前置）。

为什么要在迁移与 uvicorn 之前单独跑一遍：`assert_prod_secrets` 只在 API 启动时执行，
那时迁移已经跑完；而它把两把密钥合成一条消息，排障时看不出「缺的是哪一把、它是干什么的」。
本脚本在入口处（`scripts/ops/docker-entrypoint.sh`）先做一次只读检查：

- prod：任一 BLOCK 项存在即退出 1，**不允许"起得来但跑不通"**；
- dev/test：同一批项降级为 WARN，只提示不拦截（本地演示形态保持零门槛）；
- `--strict`：在 dev/test 上也按 prod 口径拦截，供预发演练用。

只读：不建连接、不发外部请求、不读写业务数据。

用法：
    python -m scripts.ops.preflight_env
    python -m scripts.ops.preflight_env --strict
    python -m scripts.ops.preflight_env --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass

from atlas.security.bootstrap import (
    PROD_BOOTSTRAP_PASSWORD_ENV,
    VOLATILE_STORAGE_OPT_IN_ENV,
    _MIN_SECRET_BYTES,
    _REQUIRED_PROD_SECRETS,
    demo_surface_enabled,
    prod_bootstrap_password,
    public_url_is_loopback,
    read_env_profile,
    read_public_url,
    read_secret,
    read_storage_backend,
)

#: 每把必需密钥「缺了会怎样」。名字与 `_REQUIRED_PROD_SECRETS` 一一对应，
#: `test_preflight_env` 会断言没有漏写用途，避免以后新增密钥时只报名字。
SECRET_PURPOSE = {
    "ATLAS_APPROVAL_HMAC_SECRET": (
        "审批/取消深链的 HMAC 签名；缺失时外发链接无法自证，邮件与 IM 审批腿全不可用"
    ),
    "ATLAS_MASTER_KEY": (
        "渠道凭据信封 AES-256-GCM 与 OAuth state 签名；缺失时已存凭据解不开、真实渠道接不上"
    ),
}

BOOTSTRAP_PASSWORD_PURPOSE = (
    "prod 首任管理员的引导口令；缺失时没有任何合法进门方式（prod 不播种明文种子账号）"
)

STORAGE_PURPOSE = (
    "持久化图／运行记录／审批／调度认领；prod 用 memory 时重启即清空，而 /api/ready 仍会返回 200"
)

DATABASE_URL_PURPOSE = "pg 后端的连接串；缺失时存储层无法建立连接，起得来但每个读写请求都失败"

PUBLIC_URL_PURPOSE = "审批挂起通知与外部回调拼链接的对外基址；prod 指向 loopback 时收到的链接点开不可达"

#: 只提示、不阻塞的条件项：(变量, 启用后的能力, 缺失后果)。
CONDITIONAL_NOTES = (
    (
        "OPENAI_API_KEY / LITELLM_MODEL / MODEL_HUB_URL",
        "AI 决策与自然语言生成",
        "三者都空时 LLM 节点在运行期显式 FAILED（不会静默假成功）；退款决策走确定性规则兜底",
    ),
    (
        "ATLAS_SMTP_HOST / ATLAS_SMTP_FROM / ATLAS_SMTP_PASSWORD",
        "真实邮件投递（message/send channel=email）",
        "HOST 留空时自动回退进程内演示记录；只配了 HOST 而缺 FROM/密码时，真实发信会在运行期失败",
    ),
    (
        "ATLAS_A2A_TASK_TOKEN",
        "A2A 执行 Agent 面（POST /api/a2a/tasks）",
        "prod 缺省 fail-closed（401）；这是设计行为，不是故障",
    ),
    (
        "ATLAS_METRICS_TOKEN",
        "/metrics 的 Bearer 鉴权",
        "留空＝本机无鉴权读；放到公网即等于公开指标",
    ),
)


@dataclass(frozen=True)
class Finding:
    level: str  # BLOCK / WARN / OK / INFO
    name: str
    detail: str
    purpose: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _level_for(strict: bool) -> str:
    return "BLOCK" if strict else "WARN"


def _secret_source(name: str) -> str:
    return f"{name}_FILE" if os.getenv(f"{name}_FILE") else name


def collect_findings(profile: str, strict: bool) -> list[Finding]:
    """返回完整检查结果；`strict` 决定必需项是 BLOCK 还是 WARN。"""
    findings: list[Finding] = []
    hard = _level_for(strict)
    findings.append(Finding("OK", "ATLAS_ENV", profile, "运行档位（dev/test 只提示，prod 严格拦截）"))

    try:
        storage = read_storage_backend()
    except ValueError as exc:
        storage = "invalid"
        findings.append(Finding(hard, "ATLAS_STORAGE_BACKEND", f"非法：{exc}", STORAGE_PURPOSE))

    if storage == "memory":
        opted_in = _env_on(VOLATILE_STORAGE_OPT_IN_ENV)
        if strict and not demo_surface_enabled() and not opted_in:
            findings.append(Finding(hard, "ATLAS_STORAGE_BACKEND", "memory", STORAGE_PURPOSE))
        else:
            findings.append(
                Finding(
                    "WARN",
                    "ATLAS_STORAGE_BACKEND",
                    "memory" + ("（已显式署名易失风险）" if opted_in else ""),
                    STORAGE_PURPOSE,
                )
            )
    elif storage == "pg":
        has_dsn = bool(_env_value("DATABASE_URL"))
        findings.append(
            Finding(
                "OK" if has_dsn else hard,
                "DATABASE_URL",
                "已设置" if has_dsn else "缺失",
                DATABASE_URL_PURPOSE,
            )
        )
    elif storage != "invalid":
        findings.append(Finding("OK", "ATLAS_STORAGE_BACKEND", storage, "存储后端档位"))

    for name in _REQUIRED_PROD_SECRETS:
        value = read_secret(name)
        source = _secret_source(name)
        if len(value) >= _MIN_SECRET_BYTES:
            findings.append(Finding("OK", source, f"已配置（{len(value)} 字节）", SECRET_PURPOSE[name]))
        else:
            findings.append(
                Finding(
                    hard,
                    source,
                    f"缺失或过短（{len(value)} < {_MIN_SECRET_BYTES} 字节）",
                    SECRET_PURPOSE[name],
                )
            )

    try:
        prod_bootstrap_password()
    except RuntimeError as exc:
        findings.append(Finding(hard, PROD_BOOTSTRAP_PASSWORD_ENV, str(exc), BOOTSTRAP_PASSWORD_PURPOSE))
    else:
        findings.append(Finding("OK", PROD_BOOTSTRAP_PASSWORD_ENV, "已配置且通过口令策略",
                                BOOTSTRAP_PASSWORD_PURPOSE))

    try:
        public_url = read_public_url()
    except ValueError as exc:
        findings.append(Finding(hard, "ATLAS_PUBLIC_URL", f"非法：{exc}", PUBLIC_URL_PURPOSE))
    else:
        loopback = public_url_is_loopback(public_url)
        if loopback and strict and not demo_surface_enabled():
            findings.append(Finding(hard, "ATLAS_PUBLIC_URL", f"指向回环：{public_url}", PUBLIC_URL_PURPOSE))
        else:
            findings.append(Finding("OK", "ATLAS_PUBLIC_URL", public_url, PUBLIC_URL_PURPOSE))

    if strict and demo_surface_enabled():
        findings.append(
            Finding(
                "WARN",
                "ATLAS_ENABLE_DEMO_MOCK",
                "已开启",
                "演示面在 prod 档开启：真实外部副作用被 mock，演练可用、不可当生产",
            )
        )

    for name, capability, consequence in CONDITIONAL_NOTES:
        if _any_set(name):
            findings.append(Finding("OK", name, "已配置", capability))
        else:
            findings.append(Finding("WARN", name, "未配置", consequence))

    if _env_value("ATLAS_SHOPIFY_ADMIN_BASE_URL"):
        findings.append(
            Finding(
                "WARN",
                "ATLAS_SHOPIFY_ADMIN_BASE_URL",
                "已设置",
                "仅本地冒烟用的 Shopify Admin mock 缝；prod 会被出向守卫拒绝，真实部署必须清空",
            )
        )
    return findings


def _env_value(name: str) -> str:
    return os.getenv(name, "").strip()


def _env_on(name: str) -> bool:
    return _env_value(name).lower() in {"1", "true", "yes", "on"}


def _any_set(names: str) -> bool:
    return any(_env_value(part.strip()) for part in names.split("/"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Atlas production env preflight")
    parser.add_argument("--strict", action="store_true", help="dev/test 也按 prod 口径拦截")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args()

    try:
        profile = read_env_profile()
    except ValueError as exc:
        print(f"[preflight] ATLAS_ENV 非法：{exc}", file=sys.stderr)
        return 2

    strict = args.strict or profile == "prod"
    findings = collect_findings(profile, strict)
    blocks = [item for item in findings if item.level == "BLOCK"]
    warns = [item for item in findings if item.level == "WARN"]

    if args.json:
        print(
            json.dumps(
                {
                    "profile": profile,
                    "strict": strict,
                    "blocked": len(blocks),
                    "warnings": len(warns),
                    "findings": [item.as_dict() for item in findings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        mode = "严格（缺失即拒绝启动）" if strict else "提示（dev/test 不拦截）"
        print(f"[preflight] ATLAS_ENV={profile}，模式：{mode}")
        for item in findings:
            print(f"[{item.level}] {item.name} — {item.detail}")
            print(f"          用途：{item.purpose}")

    if blocks:
        print(
            f"[preflight] 拒绝：{len(blocks)} 项必需配置不可用，进程不应启动。"
            "按上面逐条补齐后重跑本命令。",
            file=sys.stderr,
        )
        return 1
    print(f"[preflight] 通过：0 阻断、{len(warns)} 条提示。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
