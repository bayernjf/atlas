"""P2-1 split: HTTP adapter discovery, OpenAPI imports and adapter drafts.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import atlas.api.main as _main
import json
from atlas.harness.definition import AdapterDefinitionError, definition_to_dict, validate_adapter_definition
from atlas.httpapi.service import HttpApiCallError
from atlas.iam.deps import require
from atlas.iam.principals import Principal, can
from atlas.llm.structured import LLMStructuredUnavailable, generate_adapter_definition
from atlas.openapi.store import ImportStoreError
from atlas.web.i18n import localize_tool_desc, resolve_locale
from fastapi import Depends, HTTPException, Header, Request, Response
from typing import Any
from atlas.api.main import (
    AdapterDecisionRequest,
    AdapterDraftRequest,
    OpenApiCredentialsRequest,
    OpenApiSourceRequest,
    _DRY_RUN_TIMEOUT_SECONDS,
    _credential_error,
    _encrypt_credentials,
    _parse_openapi_source,
    _record_audit,
    _runtime_registry,
    _secret_provider,
)


router: APIRouter = APIRouter()


@router.post("/api/adapter-drafts", status_code=201)
def create_adapter_draft(
    http_request: Request,
    request: AdapterDraftRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """NL 造工具草稿（docs/133 BQ-D5，docs/107 §3.2 闭合）：业务描述 → AdapterDefinition 草稿。

    LLM 不可用/坏 JSON 显式 422（不静默降级）；草稿形状违例 422；落草稿后待 dry-run＋人工采纳。
    """
    services = _main.services_for(principal)
    try:
        raw = generate_adapter_definition(
            prompt=request.prompt, tenant_id=principal.tenant_id
        )
    except LLMStructuredUnavailable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        definition = validate_adapter_definition(raw)
    except AdapterDefinitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    draft = services.adapter_drafts.add(definition_to_dict(definition))
    _record_audit(services, principal, http_request, "adapter_draft.create", 201)
    return draft


@router.post("/api/adapter-drafts/{draft_id}/dry-run")
def dry_run_adapter_draft(
    http_request: Request,
    draft_id: str,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """连通校验（docs/133 BQ-D3）：对 base_url 发只读 GET，过 EgressGuard，不落运行账。

    2xx 视为可达；非 2xx/传输失败/SSRF 拒绝 → reachable=false＋原因。不改 decision 状态。
    """
    services = _main.services_for(principal)
    draft = services.adapter_drafts.get(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"适配器草稿不存在：{draft_id}")
    base_url = (draft.get("definition") or {}).get("base_url", "")
    ok = False
    reason = "base_url 为空"
    if base_url:
        client = _main.HttpApiClient(base_url=base_url)
        try:
            result = client.request(
                method="GET", url="/", timeout=_DRY_RUN_TIMEOUT_SECONDS, idempotent=True
            )
            status = int(result.get("status", 0))
            ok = 200 <= status < 300
            reason = "" if ok else f"HTTP {status}"
        except HttpApiCallError as exc:
            ok = False
            reason = f"{exc.code}: {exc}"
    from datetime import datetime, timezone as _tz

    payload = {
        "checkedAt": datetime.now(_tz.utc).isoformat(),
        "ok": ok,
        "reason": reason,
    }
    updated = services.adapter_drafts.set_reachability(draft_id, payload)
    _record_audit(services, principal, http_request, "adapter_draft.dry_run", 200)
    return updated


@router.put("/api/adapter-drafts/{draft_id}/decision")
def decide_adapter_draft(
    http_request: Request,
    draft_id: str,
    request: AdapterDecisionRequest,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """人工采纳（docs/133 BQ-D4，复用 D51 decision 形态）：adopted/dismissed、可改判。

    **F-P2 最小权限＋采纳即人审**：只有 admin 可决策；auth_type 非 none（真实执行要凭据）
    的草稿须 authConfirmed=true 显式确认才可采纳，否则 422。
    """
    services = _main.services_for(principal)
    draft = services.adapter_drafts.get(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"适配器草稿不存在：{draft_id}")
    if (
        request.decision == "adopted"
        and (draft.get("definition") or {}).get("auth_type", "none") != "none"
        and not request.authConfirmed
    ):
        raise HTTPException(
            status_code=422,
            detail="auth_type 非 none 的适配器执行需要凭据，须 admin 显式确认（authConfirmed=true）",
        )
    updated = services.adapter_drafts.set_decision(draft_id, request.decision)
    _record_audit(services, principal, http_request, "adapter_draft.decide", 200)
    return updated


@router.get("/api/adapter-drafts")
def list_adapter_drafts(
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """本租户适配器草稿（倒序；docs/133 §2.2 投影）。"""
    services = _main.services_for(principal)
    return {"items": services.adapter_drafts.list()}


@router.get("/api/adapters")
def list_adapters(
    principal: Principal = Depends(require("read")),
    accept_language: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    # 执行期注册表在全局基础设施之上合并本租户渠道适配器（docs/38 §1E）
    locale = resolve_locale(accept_language)
    adapters = _runtime_registry(_main.services_for(principal)).list_adapters()
    for adapter in adapters:
        adapter["tools"] = [localize_tool_desc(tool, locale) for tool in adapter.get("tools", [])]
    return adapters


@router.post("/api/openapi/preview")
def preview_openapi(
    raw: OpenApiSourceRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    spec = _parse_openapi_source(raw)
    return {
        "title": spec.title,
        "base_url": spec.base_url,
        "operations": [op.model_dump() for op in spec.operations],
        "security_schemes": [
            {
                "name": scheme.name,
                "kind": scheme.kind,
                "location": scheme.location,
                "param": scheme.param,
            }
            for scheme in spec.security_schemes.values()
        ],
        "imported_count": sum(not op.skipped for op in spec.operations),
        "skipped_count": sum(op.skipped for op in spec.operations),
    }


@router.post("/api/openapi/imports", status_code=201)
def import_openapi(
    raw: OpenApiSourceRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    spec = _parse_openapi_source(raw)
    if not any(not op.skipped for op in spec.operations):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "OPENAPI_NO_IMPORTABLE_OPERATION",
                "message": "没有可导入的 operation（文档内操作全部被跳过）",
            },
        )
    try:
        envelopes = _encrypt_credentials(
            raw.credentials, set(spec.security_schemes)
        )
        imported = _main.services_for(principal).openapi_imports.add(
            spec, envelopes=envelopes
        )
    except ImportStoreError as exc:
        detail: dict[str, Any] = {"code": exc.code, "message": str(exc)}
        if getattr(exc, "existing_spec_id", None):
            detail["existingSpecId"] = exc.existing_spec_id
        if getattr(exc, "params", None):
            detail["params"] = exc.params
        raise HTTPException(
            status_code=exc.status_code, detail=detail
        ) from exc
    return imported.model_dump()


@router.get("/api/openapi/imports")
def list_openapi_imports(
    include_deleted: bool = False,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    # docs/60 G2：查看含已软删条目需 administer；缺省/false 维持 read 且不返已删。
    if include_deleted and not can(principal.role, "administer"):
        raise HTTPException(status_code=403, detail="无权查看已删除的 API 规格")
    items = _main.services_for(principal).openapi_imports.list(
        include_deleted=include_deleted
    )
    return {"items": [spec.model_dump() for spec in items]}


@router.get("/api/openapi/imports/{spec_id}")
def get_openapi_import(
    spec_id: str,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    imported = _main.services_for(principal).openapi_imports.get(spec_id)
    if imported is None:
        raise HTTPException(status_code=404, detail="导入规格不存在")
    return imported.model_dump()


@router.delete("/api/openapi/imports/{spec_id}")
def delete_openapi_import(
    spec_id: str,
    hard: bool = False,
    principal: Principal = Depends(require("administer")),
) -> Response:
    store = _main.services_for(principal).openapi_imports
    if hard:
        # docs/60 G2：仅已软删记录可物理删除；未软删 → 409，不存在 → 404，成功 204。
        try:
            purged = store.purge(spec_id)
        except ImportStoreError as exc:
            # 带 params 的码要把 params 一起下发，否则英文态模板填不满回退中文原文。
            detail: dict[str, object] = {"code": exc.code, "message": str(exc)}
            if getattr(exc, "params", None):
                detail["params"] = exc.params
            raise HTTPException(status_code=exc.status_code, detail=detail) from exc
        if not purged:
            raise HTTPException(status_code=404, detail="导入规格不存在")
        return Response(status_code=204)
    if not store.delete(spec_id):
        raise HTTPException(status_code=404, detail="导入规格不存在")
    return {"deleted": True}


@router.post("/api/openapi/imports/{spec_id}/restore")
def restore_openapi_import(
    spec_id: str,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """docs/56 §3.3：恢复软删规格；与另一未删同指纹规格冲突返 409。"""
    ok, code, existing = _main.services_for(principal).openapi_imports.restore(spec_id)
    if ok:
        return {"restored": True}
    if code == "OPENAPI_RESTORE_CLASH":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "OPENAPI_RESTORE_CLASH",
                "message": "恢复后与现有未删规格内容重复",
                "existingSpecId": existing,
            },
        )
    raise HTTPException(status_code=404, detail="导入规格不存在或未被删除")


@router.put("/api/openapi/imports/{spec_id}/credentials")
def put_openapi_credentials(
    spec_id: str,
    raw: OpenApiCredentialsRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, list[str]]:
    store = _main.services_for(principal).openapi_imports
    imported = store.get(spec_id)
    if imported is None:
        raise HTTPException(status_code=404, detail="导入规格不存在")
    envelopes = dict(imported.credential_envelopes)
    for name, value in raw.credentials.items():
        if name not in imported.security_schemes:
            raise _credential_error(name)
        scheme = imported.security_schemes[name]
        if scheme.kind == "basic":
            if not isinstance(value, dict):
                envelopes.pop(name, None)
                continue
            username = value.get("username")
            password = value.get("password")
            if (
                not isinstance(username, str)
                or not username.strip()
                or not isinstance(password, str)
                or not password.strip()
            ):
                raise _credential_error(name, "Basic 鉴权需同时提供非空用户名与密码",
                               code="OPENAPI_CREDENTIAL_BASIC_INCOMPLETE")
            plaintext = json.dumps(
                {"username": username.strip(), "password": password.strip()},
                ensure_ascii=False,
            )
            envelopes[name] = _secret_provider.encrypt(plaintext)
        elif not isinstance(value, str) or not value.strip():
            envelopes.pop(name, None)
        else:
            envelopes[name] = _secret_provider.encrypt(value.strip())
    updated = store.put_credentials(spec_id, envelopes)
    if updated is None:
        raise HTTPException(status_code=404, detail="导入规格不存在")
    configured = [name for name in updated.security_schemes if name in envelopes]
    return {"configured": configured}
