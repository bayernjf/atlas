"""FastAPI 鉴权依赖（04 §5.14）：Bearer 会话 → Principal → 角色白名单 → 租户服务。"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from atlas.security.bootstrap import demo_surface_enabled, read_env_profile, read_storage_backend

from .passwords import verify_password
from .principals import (
    Capability, Principal, SEED_TENANTS, SEED_USERS, can, seed_plan_for_profile,
)
from .registry import TenantRegistry, TenantServices
from .sessions import SESSION_COOKIE, SessionStore
from .throttle import LoginThrottle


def select_session_store():
    # docs/30 §4（ADR T24）：PG 档会话落 iam_sessions 表，进程重启/多实例共享令牌；
    # 密码哈希/JWT 仍属生产鉴权批次，不在此列。
    if read_storage_backend() == "pg":
        from atlas.storage.pg import get_pg_backend

        return get_pg_backend().session_store()
    return SessionStore()


def select_user_store():
    # ADR T25（docs/31 §2）：PG 档账号落 iam_users；内存档惰性播种。
    if read_storage_backend() == "pg":
        from atlas.storage.pg import get_pg_backend

        return get_pg_backend().user_store()
    from .accounts import UserStore

    return UserStore()


session_store = select_session_store()
user_store = select_user_store()
user_store.bind_session_store(session_store)
# 两档均幂等播种（PG ON CONFLICT DO NOTHING），否则 PG 首启无管理员、无法登录。
# 播哪些账号按环境档位定：prod 只播各租户 admin＋引导口令，绝不播仓库内明文口令（docs/66）。
user_store.seed(seed_plan_for_profile())
tenant_registry = TenantRegistry()
login_throttle = LoginThrottle()

_UNAUTHENTICATED = "缺少或无效的登录凭证"
_FORBIDDEN = "当前角色无权执行此操作"

# docs/17 §2.4 第一批债：认证/鉴权错误补结构化 code（code 是契约、message 是日志/默认），
# 前端按 code 走 i18n（error.auth.*）。跨租户 404 故意不区分「不存在/越权」，不补 code。
CODE_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
CODE_ACCOUNT_DISABLED = "AUTH_ACCOUNT_DISABLED"
CODE_SEED_CREDENTIAL = "AUTH_SEED_CREDENTIAL"

# docs/64 J-1c：种子默认口令明文表（仅用于 prod 登录拒绝比对，非凭据存储）。
_SEED_PASSWORD_BY_USERNAME = {u.username: u.password for u in SEED_USERS}
CODE_UNAUTHENTICATED = "AUTH_UNAUTHENTICATED"
CODE_FORBIDDEN = "AUTH_FORBIDDEN"
CODE_PASSWORD_CHANGE_REQUIRED = "AUTH_PASSWORD_CHANGE_REQUIRED"

_PASSWORD_CHANGE_REQUIRED = "该账号仍在使用部署时下发的引导口令，请先修改密码后再使用平台"


def must_change_password(principal: Principal) -> bool:
    """首登强制改密的**唯一**判定（docs/95 打包 AV §3）。

    读用户行而不是会话里的 Principal：PG 档 `iam_sessions` 只持久化
    `(tenant_id, username, role, expires_at)`，重建出的 Principal 带不了这个标志，
    照内存档那样读就会在两档给出不同答案（docs/95 §1.2）。每请求一次主键 SELECT，
    不做现场哈希比对——bcrypt ≈100ms 进不了请求路径（docs/95 §1.1）。

    演示面豁免：`demo_surface_enabled()` 已含「非 prod 恒开」，所以 dev/test 与
    prod＋`ATLAS_ENABLE_DEMO_MOCK=1` 的行为逐键不变（docs/95 §2 D-4）。
    """
    if demo_surface_enabled():
        return False
    account = user_store.get(principal.tenant_id, principal.username)
    return account is not None and account.password_rotated_at is None


def auth_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def authenticate_login(username: str, password: str) -> Principal:
    """登录认证（docs/31 §2.2）：user_store 全局按 username 查、验哈希。

    用户不存在/口令错 → 401（不区分，防枚举）；停用账号口令正确 → 403。
    """
    account = user_store.get_by_username(username)
    if account is None or not verify_password(password, account.password_hash):
        raise auth_error(401, CODE_INVALID_CREDENTIALS, "用户名或密码错误")
    if (
        read_env_profile() == "prod"
        and password == _SEED_PASSWORD_BY_USERNAME.get(account.username)
    ):
        raise auth_error(
            403,
            CODE_SEED_CREDENTIAL,
            "生产环境禁止使用种子账号默认口令登录，请先通过管理员改密",
        )
    if account.status == "disabled":
        raise auth_error(403, CODE_ACCOUNT_DISABLED, "账号已停用，请联系管理员")
    tenant = SEED_TENANTS.get(account.tenant_id)
    return Principal(
        tenant_id=account.tenant_id,
        tenant_name=tenant.name if tenant is not None else account.tenant_id,
        username=account.username,
        display_name=account.display_name,
        role=account.role,
    )


def presented_token(request: Request) -> str | None:
    """本次请求**实际出示**的凭证，取数规则与 `get_principal` 同源（只有一份）。

    打包 ZQ Q4 之后 SPA 不带 Authorization 头走 Cookie；A2A／MCP／旧调用方带 Bearer。
    两种凭证同时出现时以头为准（键存在即认头）。这条规则以前在 `change_password`／
    `logout`／`me` 里各抄一份且互相不一致：改密时用 `_bearer_token`、吊销时用
    `_session_token`（Cookie 优先），于是"保留当前会话"保留的是**没被用来认证的那个会话**。
    """
    if "authorization" in request.headers:
        header = request.headers["authorization"]
        return header[7:].strip() if header[:7].lower() == "bearer " else None
    return request.cookies.get(SESSION_COOKIE)


def get_principal(request: Request) -> Principal:
    # 打包 ZQ Q4：前端不再落盘 token，凭证由 httpOnly Cookie 自动携带（SameSite=Strict
    # 收敛 CSRF 面）；Bearer 保留兼容 A2A/MCP 与旧调用方。取数规则见 presented_token。
    principal = session_store.principal_for_token(presented_token(request))
    if principal is None:
        raise auth_error(401, CODE_UNAUTHENTICATED, _UNAUTHENTICATED)
    # T6 审计中间件在响应后读取 request.state.principal 记录写操作（docs/35 §6）。
    request.state.principal = principal
    return principal


def require(*capabilities: Capability):
    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        # docs/95 D-2 选 (b)：业务端点不分能力档，一律先过轮换门，所以不必维护路径白名单——
        # /api/auth/* 本来就不经过 require()。轮换门排在角色门之前：先说"该改密"这个可执行
        # 的下一步，而不是让运营对着"当前角色无权"猜。
        if must_change_password(principal):
            raise auth_error(403, CODE_PASSWORD_CHANGE_REQUIRED, _PASSWORD_CHANGE_REQUIRED)
        if not all(can(principal.role, capability) for capability in capabilities):
            raise auth_error(403, CODE_FORBIDDEN, _FORBIDDEN)
        return principal

    return dependency


def services_for(principal: Principal) -> TenantServices:
    return tenant_registry.get(principal.tenant_id)
