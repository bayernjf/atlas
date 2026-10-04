# -*- coding: utf-8 -*-
"""ATLAS_ENV 环境档位与 prod fail-closed 密钥门（docs/64 打包 J，J-1a）。

根因（docs/63 S7/S1）：src/atlas 从不读 ATLAS_ENV，缺密钥只 warning 落到仓库内
写死的 dev 常量——"配置不安全照样起得来"。本模块是唯一档位入口：

- read_env_profile() 读 ATLAS_ENV（dev/test/prod，缺省 dev，非法值 raise）；
- read_storage_backend() 读 ATLAS_STORAGE_BACKEND（memory/pg，缺省 memory，非法值 raise）；
- demo_surface_enabled() demo 演示面总开关（非 prod 恒开；prod 仅显式开关才开）；
- assert_prod_secrets() 在 prod 下缺任一必需密钥（或 <32 字节）即 raise，
  由 api 启动处调用，prod 缺密钥拒绝启动（fail-closed）；
- read_secret() 是**所有**密钥读取的唯一入口：同名 `<NAME>_FILE` 指向文件时读文件
  （docker secret／vault 渲染落盘形态），否则回退同名环境变量（docs/08 打包 ZO，
  docs/73 1.1「凭据由 vault 注入、绝不明文 .env」的落点）。
- read_public_url() 是 `ATLAS_PUBLIC_URL`（应用对外入口：审批深链／Shopify 回调）的**唯一**
  读取器，缺省回退 `DEFAULT_PUBLIC_URL`（docs/89 §15 A-6：此前同一 env 与同一缺省散在三处）。

非 prod 一律静默通过，本地/演示行为不变。
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

_VALID_PROFILES = ("dev", "test", "prod")

#: 存储后端档位（docs/24 §1.2②）：进程内 memory（默认/测试）或持久化 pg。
#: 与 ATLAS_ENV 同属"档位读取器"——大小写不敏感、非法值 raise（拒启而非静默降级）。
_VALID_STORAGE_BACKENDS = ("memory", "pg")

# prod 下必须配置的两把密钥（≥32 字节），缺任一即拒绝启动。
_REQUIRED_PROD_SECRETS = ("ATLAS_APPROVAL_HMAC_SECRET", "ATLAS_MASTER_KEY")
_MIN_SECRET_BYTES = 32

#: prod 首任管理员的引导口令环境变量（docs/66 打包 L，补 docs/64 J-1c 的进门缺口）。
PROD_BOOTSTRAP_PASSWORD_ENV = "ATLAS_ADMIN_BOOTSTRAP_PASSWORD"


def read_secret(name: str) -> str:
    """读取一个密钥：优先 `{name}_FILE` 指向的文件，否则回退同名环境变量。

    文件形态（docker secret 挂载点／vault 渲染落盘）的目的正是**不让明文进 env**，
    所以 `_FILE` 一旦设了就必须拿到值：文件不存在／不可读／内容为空 ⇒ 一律按**缺失**
    返回空串，**不**静默回退同名 env——回退会把"挂载没生效"退化成"静默用旁边那份
    旧明文"，那正是本函数要防的事（docs/08 打包 ZO D-①）。

    文件尾部单个换行会被剥掉（`echo`/vault 渲染普遍带尾换行，不剥会让 32 字节的密钥
    变成 33 字节、或让口令校验失败）；**只剥一个**，密钥本身含尾空白属配置错误。
    """
    file_ref = os.getenv(f"{name}_FILE", "").strip()
    if not file_ref:
        return os.getenv(name, "").strip()
    try:
        # 字节级读取后显式解码，**不**用 read_text：后者默认 universal-newline，
        # 读取时把 CRLF/CR 统一折成 LF，会令下面 endswith("\r\n") 的 CRLF 剥 2 分支
        # 永不命中（密钥本体里若含 CR 还会被隐式改写）。docker secret / vault 落盘
        # 可能是 CRLF，必须原样读到字节、再按"恰好一个尾行终止符"精确剥除。
        raw = Path(file_ref).read_bytes().decode("utf-8")
    except OSError:
        return ""
    if raw.endswith("\r\n"):
        return raw[:-2]
    return raw[:-1] if raw.endswith("\n") else raw


def read_env_profile() -> str:
    """读取 ATLAS_ENV；缺省 dev，非法值 raise ValueError。大小写不敏感。"""
    raw = os.getenv("ATLAS_ENV", "dev").strip().lower()
    if raw not in _VALID_PROFILES:
        raise ValueError(
            f"ATLAS_ENV 非法值 {raw!r}，合法值 {list(_VALID_PROFILES)}"
        )
    return raw


def read_storage_backend() -> str:
    """读取 ATLAS_STORAGE_BACKEND；缺省 memory，非法值 raise ValueError。大小写不敏感。

    docs/77 R4：此前三处是裸比较（直读环境变量再与 "pg" 比对），不经校验——声明 prod
    却把值写成 `postgres`／拼错时，进程照起、全部状态落在 RAM、重启即失，而 `/api/ready`
    仍 200。本函数是唯一读取入口：非法值当场拒启，合法值归一。
    """
    raw = os.getenv("ATLAS_STORAGE_BACKEND", "memory").strip().lower()
    if raw not in _VALID_STORAGE_BACKENDS:
        raise ValueError(
            f"ATLAS_STORAGE_BACKEND 非法值 {raw!r}，合法值 {list(_VALID_STORAGE_BACKENDS)}"
        )
    return raw


def demo_surface_enabled() -> bool:
    """demo 演示面总开关（docs/75 打包 P；docs/77 R2/R5 复用同一判定）。

    非 prod（dev/test）恒开；prod 默认关（fail-closed），仅 `ATLAS_ENABLE_DEMO_MOCK=1`
    显式开。档位一律走 `read_env_profile()`（大小写不敏感、拒非法值）。HTTP mock 路由、
    运行期演示适配器（shop/database/web 真浏览器）、FastAPI 文档面与渠道出向测试缝共用这一处。
    """
    return read_env_profile() != "prod" or os.getenv("ATLAS_ENABLE_DEMO_MOCK") == "1"


#: 应用对外入口的缺省地址（本地前端 dev server）。prod 用它＝深链不可达。
DEFAULT_PUBLIC_URL = "http://localhost:5174"

#: 只有本机听得见的主机名——签名深链发到这里等于把令牌投递给"任何本地监听者"。
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")


def read_public_url() -> str:
    """`ATLAS_PUBLIC_URL` 的唯一读取器（docs/89 §15 A-6）：缺省回退、尾斜杠归一。

    此前同一 env 加同一缺省散在三处（api 深链、Shopify 回调地址、通知器缺省常量），
    改一处漏两处；收敛到这里，消费点只做一件事。空白值按"没配"处理——`ATLAS_PUBLIC_URL="  "`
    拼出来的深链是 `/approvals/…` 这种相对形状，比回落缺省更难排查。
    """
    return ((os.getenv("ATLAS_PUBLIC_URL") or "").strip() or DEFAULT_PUBLIC_URL).rstrip("/")


def public_url_is_loopback(url: str) -> bool:
    """这个对外地址其实只有本机听得见吗（docs/89 §15 A-6）。"""
    return (urlsplit(url).hostname or "").lower() in _LOOPBACK_HOSTS


def prod_bootstrap_password() -> str | None:
    """prod 的首任管理员引导口令；非 prod 返回 None（演示/测试形态零变化）。

    prod 下缺失或不过口令策略即 raise——这是 fail-closed 的正身：宁可不起来，也不要起来一个
    "谁都进不去"或"仓库明文口令可登"的实例（docs/63 §0A N1）。口令只校验、不回显、不写日志。
    """
    if read_env_profile() != "prod":
        return None
    from atlas.iam.passwords import validate_password  # 函数级导入，避开 iam→security 环

    raw = read_secret(PROD_BOOTSTRAP_PASSWORD_ENV).strip()
    if not raw:
        # 与 assert_prod_secrets 同口径：点名实际在用的那个变量（_FILE 挂了就报 _FILE）。
        source = (
            f"{PROD_BOOTSTRAP_PASSWORD_ENV}_FILE"
            if os.getenv(f"{PROD_BOOTSTRAP_PASSWORD_ENV}_FILE")
            else PROD_BOOTSTRAP_PASSWORD_ENV
        )
        raise RuntimeError(
            f"ATLAS_ENV=prod 拒绝启动：缺少 {source}"
            "（prod 不播种仓库内明文口令，首任管理员需要引导口令，见 docs/66）"
        )
    try:
        validate_password(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"ATLAS_ENV=prod 拒绝启动：{PROD_BOOTSTRAP_PASSWORD_ENV} 不合口令策略——{exc}"
        ) from exc
    return raw


#: 启动时从 `*_FILE` 补水进 os.environ 的键（docs/08 打包 ZO D-③）。
#: 只列**应用改不了其读取点**的那一类：litellm 直连 OpenAI 兼容端点时自己读
#: `OPENAI_API_KEY`（`litellm/llms/openai/common_utils.py:352-357`），我们无法让它改走
#: read_secret，只能启动时把文件内容补进 env；已在 env 的不覆盖（env 优先＝不制造
#: 第二个真值来源）。Atlas 自己的密钥一律走 read_secret，不进这张表。
_HYDRATED_ENV_KEYS = ("OPENAI_API_KEY",)


def hydrate_file_secrets() -> None:
    """把 `*_FILE` 指向的内容补进同名环境变量（幂等；env 已有值则不动）。"""
    for name in _HYDRATED_ENV_KEYS:
        if os.getenv(name):
            continue
        value = read_secret(name)
        if value:
            os.environ[name] = value


def assert_prod_secrets() -> None:
    """prod 下缺任一必需密钥（或 <32 字节）即 raise RuntimeError（fail-closed）。"""
    if read_env_profile() != "prod":
        return
    missing: list[str] = []
    for name in _REQUIRED_PROD_SECRETS:
        if len(read_secret(name)) < _MIN_SECRET_BYTES:
            # 点名实际在用的那个变量：`_FILE` 挂了却报 env 名，排障会查错地方。
            missing.append(f"{name}_FILE" if os.getenv(f"{name}_FILE") else name)
    if missing:
        raise RuntimeError(
            "ATLAS_ENV=prod 拒绝启动：缺少或过短的必需密钥 "
            f"{missing}（各需 ≥{_MIN_SECRET_BYTES} 字节随机值，见 .env.example）"
        )
    # 引导口令单独一条消息：它缺的不是"密钥"而是"进门方式"，混在一起会误导排障。
    prod_bootstrap_password()


#: 显式允许 prod 跑易失存储的逃生门（探针/演示形态）；缺省不放。
VOLATILE_STORAGE_OPT_IN_ENV = "ATLAS_ALLOW_VOLATILE_STORAGE"


def assert_prod_storage_backend() -> None:
    """prod 且未显式 opt-in 时，`ATLAS_STORAGE_BACKEND=memory` 拒绝启动（docs/89 §16 A-11）。

    这不是新规矩而是**执行已有规矩**：`.env.example:28` 早就写着"prod 必须 pg，否则全部状态
    落在内存、重启即失"，但全仓没有任何地方 enforcement——写错/忘配时进程照常起来、
    `/api/ready` 照常 200，图与审批与运行记录活到下一次重启为止。同族先例＝docs/77 R4
    （非法档位值拒启）与 docs/66（prod 缺引导口令拒启）：**档位不能靠缺省兜出来**。

    三条合法出路，全部要求"显式"：① 设 `pg`（出厂 docker-compose 即如此）；② 开演示面
    （`ATLAS_ENABLE_DEMO_MOCK=1`，与 shop/database/web 同一处判定）；③ 明知要易失存储仍要
    起——设 `ATLAS_ALLOW_VOLATILE_STORAGE=1`（探针与非持久用途走这条，写下来就是一次署名）。
    """
    if read_env_profile() != "prod":
        return
    if read_storage_backend() != "memory":
        return
    if demo_surface_enabled():
        return
    if os.getenv(VOLATILE_STORAGE_OPT_IN_ENV, "").strip() == "1":
        return
    raise RuntimeError(
        "ATLAS_ENV=prod 拒绝启动：ATLAS_STORAGE_BACKEND=memory 会把全部业务状态（图／运行记录／"
        "审批／调度认领）只留在进程内存里，重启即失，而 /api/ready 仍会返回 200。"
        "请设 ATLAS_STORAGE_BACKEND=pg（见 .env.example:28）；确需易失形态时显式开演示面 "
        f"ATLAS_ENABLE_DEMO_MOCK=1，或署名承担风险：{VOLATILE_STORAGE_OPT_IN_ENV}=1。"
    )
