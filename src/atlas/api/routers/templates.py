"""P2-1 split: Graph, message and alert-rule template routes.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import atlas.api.main as _main
import copy
import httpx
import json
from atlas.graph.dsl import parse_graph
from atlas.iam.deps import require
from atlas.iam.principals import Principal
from atlas.message.template_store import MessageTemplateNameConflict
from atlas.monitoring.rule_templates import get_rule_template, list_rule_templates
from atlas.monitoring.rule_user_store import RuleTemplateNameConflict
from atlas.security.egress import EgressDenied
from atlas.template import get_template, list_templates
from atlas.template.user_store import TemplateVersionConflict
from atlas.web.i18n import localize_template, resolve_locale
from fastapi import Depends, HTTPException, Header, Response
from typing import Any
from atlas.api.main import (
    MessageTemplateCreateRequest,
    RuleTemplateCreateRequest,
    TemplateImportRequest,
    TemplateInstantiateRequest,
    UserTemplateCreateRequest,
    UserTemplateUpdateRequest,
    _TEMPLATE_IMPORT_MAX_BYTES,
    _TEMPLATE_IMPORT_TIMEOUT,
    _message_template_payload,
    _rule_template_payload,
    _template_import_egress,
    _template_matches_query,
    _validate_instantiate_values,
    validate_template_params,
)


router: APIRouter = APIRouter()


@router.get("/api/templates")
def list_catalog_templates(
    principal: Principal = Depends(require("read")),
    accept_language: str | None = Header(default=None),
    q: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """列出模板（内置目录 04 §5.10 在前、租户自建模板 docs/85 在后；列表投影不含 graph）；
    内置 name/description 按 Accept-Language 本地化（docs/70）。
    打包 A1（docs/97）：可选 q 过滤（两段各自按 name/description/tags/category casefold 子串任一命中），
    q 缺失/空白＝全量；用户段投影增 version/updated_at/usage_count，内置段 version 恒 1。"""
    locale = resolve_locale(accept_language)
    query = (q or "").strip().casefold()
    items = [
        localize_template(
            {
                "id": template.id,
                "name": template.name,
                "description": template.description,
                "tags": template.tags,
                "category": template.category,
                "node_count": len(template.graph["nodes"]),
                "source": "catalog",
                "deletable": False,
                "version": 1,
            },
            locale,
        )
        for template in list_templates()
        if not query or _template_matches_query(template.model_dump(), query)
    ]
    items.extend(
        {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "tags": template.tags,
            "category": template.category,
            "node_count": len(template.graph.get("nodes", [])),
            "source": "user",
            "deletable": True,
            "created_at": template.created_at,
            "version": template.version,
            "updated_at": template.updated_at,
            "usage_count": template.usage_count,
        }
        for template in _main.services_for(principal).user_templates.list()
        if not query or _template_matches_query(template.model_dump(), query)
    )
    return {"items": items}


@router.post("/api/templates", status_code=201)
def create_user_template(
    body: UserTemplateCreateRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """画布另存为租户私有模板（docs/85 D-2）：parse_graph 仅校验可编译，图原样存。
    A1（docs/97）：body 可带 params（参数化声明，形状校验失败 422 且模板未变）。"""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="模板名称不能为空")
    tags = [tag.strip() for tag in body.tags]
    for tag in tags:
        if not 1 <= len(tag) <= 20:
            raise HTTPException(status_code=422, detail="标签长度须在 1-20 字符之间")
    category = body.category.strip()
    if not category:
        category = ""
    elif len(category) > 30:
        raise HTTPException(status_code=422, detail="分类长度须在 30 字符以内")
    params_errors = validate_template_params(body.params)
    if params_errors:
        raise HTTPException(status_code=422, detail="；".join(params_errors))
    parse_graph(body.graph)
    template = _main.services_for(principal).user_templates.add(
        name=name,
        description=body.description,
        tags=tags,
        category=category,
        graph=body.graph,
        params=body.params,
    )
    return {**template.model_dump(), "source": "user", "deletable": True}


@router.put("/api/templates/{template_id}")
def update_user_template(
    template_id: str,
    body: UserTemplateUpdateRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """整体更新租户私有模板（docs/86 D-1）：id/seq/created_at 不变；
    tags 字段缺省＝保留旧值（D-5 唯一例外），内置 id 与不存在统一 404。
    A1（docs/97）：可选 `if_match_version` CAS——不匹配 409 `TEMPLATE_VERSION_CONFLICT`（缺省无防护）；
    `params` 缺省＝保留旧值（照 tags 先例）。"""
    store = _main.services_for(principal).user_templates
    if get_template(template_id) is not None:
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    current = store.get(template_id)
    if current is None:
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="模板名称不能为空")
    tags = current.tags if body.tags is None else [tag.strip() for tag in body.tags]
    for tag in tags:
        if not 1 <= len(tag) <= 20:
            raise HTTPException(status_code=422, detail="标签长度须在 1-20 字符之间")
    category = current.category if body.category is None else body.category.strip()
    if len(category) > 30:
        raise HTTPException(status_code=422, detail="分类长度须在 30 字符以内")
    if body.params is not None:
        params_errors = validate_template_params(body.params)
        if params_errors:
            raise HTTPException(status_code=422, detail="；".join(params_errors))
    parse_graph(body.graph)
    try:
        updated = store.update(
            template_id,
            name=name,
            description=body.description,
            tags=tags,
            category=category,
            graph=body.graph,
            params=body.params,
            if_match_version=body.if_match_version,
        )
    except TemplateVersionConflict:
        raise HTTPException(
            status_code=409,
            detail=f"模板已被他人更新（当前版本 {current.version}），请刷新后重试",
        ) from None
    return {**updated.model_dump(), "source": "user", "deletable": True}


@router.delete("/api/templates/{template_id}")
def delete_user_template(
    template_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, bool]:
    """仅删用户模板（docs/85 D-3）：内置 id 与不存在 id 统一 404。"""
    if get_template(template_id) is not None:
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    if not _main.services_for(principal).user_templates.delete(template_id):
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    return {"deleted": True}


@router.post("/api/templates/{template_id}/usage")
def touch_template_usage(
    template_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, int]:
    """A1 使用统计（docs/97 E-4）：显式 touch 计数 +1，返回新值；
    内置/不存在/跨租户统一 404（内置 v1 不计数）。"""
    if get_template(template_id) is not None:
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    store = _main.services_for(principal).user_templates
    if not store.touch(template_id):
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    updated = store.get(template_id)
    return {"usage_count": updated.usage_count}


@router.post("/api/templates/{template_id}/instantiate")
def instantiate_user_template(
    template_id: str,
    body: TemplateInstantiateRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """A1 参数化实例化（docs/97 E-6）：校验 required/类型/options/未知参数名后返回
    模板图深拷贝，values 覆写进 graph.variables（命中 name 覆写 value、未命中 append
    {name,type,value,scope:"global"}）；模板原图逐键不变；内置/不存在/跨租户 404。"""
    if get_template(template_id) is not None:
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    store = _main.services_for(principal).user_templates
    template = store.get(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    value_errors = _validate_instantiate_values(template.params, body.values)
    if value_errors:
        raise HTTPException(status_code=422, detail="；".join(value_errors))
    graph = copy.deepcopy(template.graph)
    variables = list(graph.get("variables", []))
    for pname, pdecl in template.params.items():
        if pname not in body.values:
            continue
        value = body.values[pname]
        found = False
        for var in variables:
            if isinstance(var, dict) and var.get("name") == pname:
                var["value"] = value
                found = True
                break
        if not found:
            variables.append(
                {
                    "name": pname,
                    "type": pdecl.get("type", "string"),
                    "value": value,
                    "scope": "global",
                }
            )
    graph["variables"] = variables
    return {
        "graph": graph,
        "template": {"id": template.id, "name": template.name, "version": template.version},
    }


@router.get("/api/templates/{template_id}")
def get_catalog_template(
    template_id: str,
    principal: Principal = Depends(require("read")),
    accept_language: str | None = Header(default=None),
) -> dict[str, Any]:
    """返回模板完整元数据（含 graph），未知 id 404（04 §5.10；用户模板 docs/85）；
    内置 name/description 按 Accept-Language 本地化（docs/70）。"""
    template = get_template(template_id)
    if template is not None:
        return localize_template(
            {**template.model_dump(), "source": "catalog", "deletable": False},
            resolve_locale(accept_language),
        )
    user_template = _main.services_for(principal).user_templates.get(template_id)
    if user_template is None:
        raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
    return {**user_template.model_dump(), "source": "user", "deletable": True}


@router.get("/api/templates/{template_id}/export")
def export_template_package(
    template_id: str,
    principal: Principal = Depends(require("read")),
) -> Response:
    """导出模板包 JSON（打包 ZM）：内置/用户模板均可，read 档。

    格式 atlas-template-v1：{format, meta: {name, description, tags, category}, graph}；
    attachment 文件名 {id}.atlas-template.json；未知 id 404。
    """
    template = get_template(template_id)
    if template is not None:
        meta = {
            "name": template.name,
            "description": template.description,
            "tags": template.tags,
            "category": template.category,
        }
        graph = template.graph
    else:
        user = _main.services_for(principal).user_templates.get(template_id)
        if user is None:
            raise HTTPException(status_code=404, detail=f"模板不存在：{template_id}")
        meta = {
            "name": user.name,
            "description": user.description,
            "tags": user.tags,
            "category": user.category,
        }
        graph = user.graph
    package = {"format": "atlas-template-v1", "meta": meta, "graph": graph}
    filename = f"{template_id}.atlas-template.json"
    return Response(
        content=json.dumps(package, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/templates/import", status_code=201)
def import_template_package(
    body: TemplateImportRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """导入模板包（打包 ZM）：body 二选一——package 直接包 / url 远程拉取。

    远程拉取：仅 https、先过 EgressGuard（与 httpapi 同一份策略/env）、
    10s 超时、不跟随重定向、响应体 ≤256KB；meta/graph 校验同创建端点。
    """
    if (body.package is None) == (body.url is None):
        raise HTTPException(status_code=422, detail="必须且只能提供 package 或 url 之一")
    if body.url is not None:
        if not body.url.startswith("https://"):
            raise HTTPException(status_code=422, detail="仅支持 https 远程导入")
        try:
            _template_import_egress.check(body.url)
            with httpx.Client(follow_redirects=False) as client:
                response = client.get(body.url, timeout=_TEMPLATE_IMPORT_TIMEOUT)
        except (EgressDenied, httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"远程模板拉取失败：{exc}") from exc
        if response.status_code >= 400:
            raise HTTPException(
                status_code=422, detail=f"远程模板拉取失败：HTTP {response.status_code}"
            )
        if len(response.content) > _TEMPLATE_IMPORT_MAX_BYTES:
            raise HTTPException(status_code=422, detail="远程模板包超过 256KB 上限")
        try:
            raw = response.json()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="远程内容不是合法 JSON") from exc
    else:
        raw = body.package or {}
    if raw.get("format") != "atlas-template-v1":
        raise HTTPException(status_code=422, detail="不认识的模板包格式（需 atlas-template-v1）")
    meta = raw.get("meta")
    if not isinstance(meta, dict):
        raise HTTPException(status_code=422, detail="模板包缺少 meta")
    name = str(meta.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=422, detail="模板名称不能为空")
    if not 1 <= len(name) <= 60:
        raise HTTPException(status_code=422, detail="模板名称长度须在 1-60 字符之间")
    description = str(meta.get("description", "")).strip()
    if len(description) > 200:
        raise HTTPException(status_code=422, detail="模板描述长度须在 200 字符以内")
    tags_raw = meta.get("tags", [])
    if not isinstance(tags_raw, list) or len(tags_raw) > 8:
        raise HTTPException(status_code=422, detail="标签须为列表且不超过 8 个")
    tags = [str(tag).strip() for tag in tags_raw]
    for tag in tags:
        if not 1 <= len(tag) <= 20:
            raise HTTPException(status_code=422, detail="标签长度须在 1-20 字符之间")
    category = str(meta.get("category", "")).strip()
    if len(category) > 30:
        raise HTTPException(status_code=422, detail="分类长度须在 30 字符以内")
    graph = raw.get("graph")
    if not isinstance(graph, dict):
        raise HTTPException(status_code=422, detail="模板包缺少 graph")
    parse_graph(graph)
    template = _main.services_for(principal).user_templates.add(
        name=name, description=description, tags=tags, category=category, graph=graph
    )
    return {**template.model_dump(), "source": "user", "deletable": True}


@router.get("/api/message-templates")
def list_message_templates(
    principal: Principal = Depends(require("read")),
    kind: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """消息通知模板列表（打包 A2，docs/98 §2.3）：投影 id/name/kind/subject/variables/updated_at，
    **不含 body**；可选 `?kind=` 过滤（approval/alert）。"""
    items = [
        {
            "id": template.id,
            "name": template.name,
            "kind": template.kind,
            "subject": template.subject,
            "variables": template.variables,
            "updated_at": template.updated_at,
        }
        for template in _main.services_for(principal).message_template_store.list(kind=kind)
    ]
    return {"items": items}


@router.get("/api/message-templates/{template_id}")
def get_message_template(
    template_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """消息模板详情（含 body）；未知/跨租户 404。"""
    template = _main.services_for(principal).message_template_store.get(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail=f"消息模板不存在：{template_id}")
    return template.model_dump()


@router.post("/api/message-templates", status_code=201)
def create_message_template(
    body: MessageTemplateCreateRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """创建消息通知模板（docs/98 §2.3）：形状校验 422（含正文占位 ⊆ variables 声明）、
    租户内重名 409 `MESSAGE_TEMPLATE_NAME_CONFLICT`。"""
    payload = _message_template_payload(body)
    try:
        template = _main.services_for(principal).message_template_store.add(**payload)
    except MessageTemplateNameConflict:
        # 结构化 detail（docs/125 §3.5，项目级审查发现 #7）：补齐 docstring 已承诺的契约。
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MESSAGE_TEMPLATE_NAME_CONFLICT",
                "message": f"消息模板名称已存在：{payload['name']}",
                "params": {"name": payload["name"]},
            },
        ) from None
    return template.model_dump()


@router.put("/api/message-templates/{template_id}")
def update_message_template(
    template_id: str,
    body: MessageTemplateCreateRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """全量更新消息模板（docs/98 §2.3）：id/created_at 不变；未知 404；撞名 409。"""
    payload = _message_template_payload(body)
    try:
        updated = _main.services_for(principal).message_template_store.update(
            template_id, **payload
        )
    except MessageTemplateNameConflict:
        # 结构化 detail（docs/125 §3.5，项目级审查发现 #7）：与 create 同形。
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MESSAGE_TEMPLATE_NAME_CONFLICT",
                "message": f"消息模板名称已存在：{payload['name']}",
                "params": {"name": payload["name"]},
            },
        ) from None
    if updated is None:
        raise HTTPException(status_code=404, detail=f"消息模板不存在：{template_id}")
    return updated.model_dump()


@router.delete("/api/message-templates/{template_id}")
def delete_message_template(
    template_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, bool]:
    """删除消息模板（docs/98 §2.3）：引用侧不阻断——消费点下次发送回退默认正文。"""
    if not _main.services_for(principal).message_template_store.delete(template_id):
        raise HTTPException(status_code=404, detail=f"消息模板不存在：{template_id}")
    return {"deleted": True}


@router.get("/api/alert-rule-templates")
def list_alert_rule_templates(
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """列出告警规则模板＝内置目录＋用户自建合并（打包 ZS，docs/102 §2.4）：投影
    id/name/description/tags/source，**不含 config**（照 docs/59 F-1 列表投影惯例）；
    内置项 source=builtin、用户项 source=user；新用户模板在前。"""
    builtin = [
        {
            "id": tpl.id,
            "name": tpl.name,
            "description": tpl.description,
            "tags": tpl.tags,
            "source": "builtin",
        }
        for tpl in list_rule_templates()
    ]
    user = [
        {
            "id": tpl.id,
            "name": tpl.name,
            "description": tpl.description,
            "tags": tpl.tags,
            "source": "user",
        }
        for tpl in _main.services_for(principal).rule_template_store.list()
    ]
    return {"items": [*user, *builtin]}


@router.get("/api/alert-rule-templates/{template_id}")
def get_alert_rule_template(
    template_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """返回告警规则模板完整元数据（含可直接 PUT rules 的 config）＝内置目录＋用户
    自建合并（打包 ZS，docs/102 §2.4）：内置 id 命中内置只读目录（source=builtin），
    否则查本租户用户 store（source=user）；均未命中 404（跨租户天然 404）。"""
    builtin = get_rule_template(template_id)
    if builtin is not None:
        return {**builtin.model_dump(), "source": "builtin"}
    user = _main.services_for(principal).rule_template_store.get(template_id)
    if user is not None:
        return {**user.model_dump(), "source": "user"}
    raise HTTPException(status_code=404, detail=f"告警规则模板不存在：{template_id}")


@router.post("/api/alert-rule-templates")
def create_alert_rule_template(
    body: RuleTemplateCreateRequest,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """新建用户告警规则模板（打包 ZS，docs/102 §2.4）：id=urt-{租户内 seq}；
    租户内重名 409 `RULE_TEMPLATE_NAME_CONFLICT`。"""
    payload = _rule_template_payload(body)
    try:
        template = _main.services_for(principal).rule_template_store.add(**payload)
    except RuleTemplateNameConflict:
        raise HTTPException(
            status_code=409, detail=f"告警规则模板名称已存在：{payload['name']}"
        ) from None
    return {**template.model_dump(), "source": "user"}


@router.put("/api/alert-rule-templates/{template_id}")
def update_alert_rule_template(
    template_id: str,
    body: RuleTemplateCreateRequest,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    """整体替换用户告警规则模板（打包 ZS，docs/102 §2.4）：id/created_at 不变；
    内置模板 id 一律 404 不可改；未知 404；撞名 409。"""
    if get_rule_template(template_id) is not None:
        raise HTTPException(status_code=404, detail=f"内置告警规则模板不可修改：{template_id}")
    payload = _rule_template_payload(body)
    try:
        updated = _main.services_for(principal).rule_template_store.update(template_id, **payload)
    except RuleTemplateNameConflict:
        raise HTTPException(
            status_code=409, detail=f"告警规则模板名称已存在：{payload['name']}"
        ) from None
    if updated is None:
        raise HTTPException(status_code=404, detail=f"告警规则模板不存在：{template_id}")
    return {**updated.model_dump(), "source": "user"}


@router.delete("/api/alert-rule-templates/{template_id}")
def delete_alert_rule_template(
    template_id: str, principal: Principal = Depends(require("administer"))
) -> dict[str, bool]:
    """删除用户告警规则模板（打包 ZS，docs/102 §2.4）：内置模板 id 一律 404 不可删；
    已应用的引用侧不阻断（消费点下次评估照旧规则集）。"""
    if get_rule_template(template_id) is not None:
        raise HTTPException(status_code=404, detail=f"内置告警规则模板不可删除：{template_id}")
    if not _main.services_for(principal).rule_template_store.delete(template_id):
        raise HTTPException(status_code=404, detail=f"告警规则模板不存在：{template_id}")
    return {"deleted": True}
