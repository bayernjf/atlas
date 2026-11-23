"""自然语言 → 流程草稿（docs/08 §7.3 验收 6；REST POST /api/nl/generate）。

配置 LITELLM_MODEL 时由 LLM 产出 Graph JSON（version 1）；
未配置或模型输出无法解析时，用退款意图规则模板兜底，
保证 Demo 离线可用。返回值为可直接回显画布的 SerializedGraph 字典。
"""


import json
import logging
import os
import re
from typing import Any

from atlas.harness.registry import AdapterRegistry

from atlas.llm.metering import complete_with_usage
from atlas.security.bootstrap import demo_surface_enabled
from atlas.template.graphs import refund_template_graph

logger = logging.getLogger(__name__)

_REFUND_KEYWORDS = ("退款", "退货", "售后")

# A-06：单张图的完整 JSON 约 2.6k 字符；1024 会在长流程上截断成非法 JSON。
_MAX_TOKENS = 4096

_TYPE_CHECKS = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
}


def generate_graph(
    prompt: str,
    tenant_id: str | None = None,
    registry: AdapterRegistry | None = None,
) -> dict[str, Any]:
    """返回可直接回显画布的 Graph 草稿（兼容既有调用方；元信息走 generate_graph_with_meta）。"""
    graph, _meta = generate_graph_with_meta(prompt, tenant_id=tenant_id, registry=registry)
    return graph


def generate_graph_with_meta(
    prompt: str,
    tenant_id: str | None = None,
    registry: AdapterRegistry | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A-06：生成草稿并返回可审计的生成来源。

    mode=llm 才代表真实模型产出；mode=template 是明确标注的离线兜底，
    不得在评估里算成 AI 成功。
    """
    model, api_key, base_url = _resolve_llm_config(tenant_id)
    if model:
        attempts = 0
        last_graph: dict[str, Any] | None = None
        last_report: dict[str, Any] | None = None
        for attempt in range(2):
            attempts += 1
            generated = _generate_with_llm(
                prompt,
                model,
                api_key=api_key,
                base_url=base_url,
                repair=last_report,
            )
            if generated is None:
                last_report = {"errors": ["输出无法解析为合法 JSON"]}
                continue
            generated = normalize_generated_graph(generated)
            last_graph = generated
            report = (
                validate_generated_graph(generated, registry)
                if registry is not None
                else {"ok": True, "errors": [], "codes": []}
            )
            last_report = report
            if report["ok"]:
                return generated, {
                    "mode": "llm",
                    "model": model,
                    "attempts": attempts,
                    "repaired": attempt > 0,
                    "compileOk": True,
                }
        if last_graph is not None:
            return last_graph, {
                "mode": "llm",
                "model": model,
                "attempts": attempts,
                "repaired": attempts > 1,
                "compileOk": False,
                "errors": list(last_report.get("errors", []) if last_report else []),
            }
    # docs/73 W1-1.1 / W5-5.4 同族：prod 档未配置 LLM 时，NL 生成不许静默降级到写死的
    # 退款模板（那等于假装调了 LLM 出图），与 ai_decision / condition(llm) 在 prod 无模型时
    # 显式报错保持同一 fail-closed 姿势。dev/demo 仍保留离线模板兜底，但在 meta 里明示。
    if not model and not demo_surface_enabled():
        raise ValueError(
            "生产环境未配置 LLM（LITELLM_MODEL 或租户默认模型），自然语言生成拒绝静默降级到"
            "规则模板；请配置 LITELLM_MODEL 与 OPENAI_API_KEY / OPENAI_BASE_URL，或显式设置 "
            "ATLAS_ENABLE_DEMO_MOCK=1 以在演示实例上启用模拟生成。"
        )
    if any(keyword in prompt for keyword in _REFUND_KEYWORDS):
        return refund_template_graph(), {
            "mode": "template",
            "model": model or None,
            "attempts": 0,
            "repaired": False,
            "compileOk": True,
            "fallbackReason": "llm_unavailable_or_invalid",
        }
    raise ValueError("未能识别流程意图（规则兜底仅支持退款/售后场景；配置 LITELLM_MODEL 可支持任意描述）")


def _resolve_llm_config(
    tenant_id: str | None,
) -> tuple[str, str | None, str | None]:
    """按优先级解析构造期默认模型（W5-3 收口）：BYOK > 单条内置 > hub > env。

    返回 ``(model, api_key_plaintext, base_url)``；无任何配置时 model 为空串（调用方走规则兜底）。
    """
    from atlas.llm.hub import resolve_runtime_model

    rt = resolve_runtime_model(tenant_id)
    if rt is None:
        return "", None, None
    return rt.model, rt.api_key, rt.base_url


def validate_param_fills(
    graph: dict[str, Any], tool_input_schemas: dict[str, dict[str, Any]]
) -> list[str]:
    """NL 草稿 tool_call 参数填充的尽力校验（04 §4.9 ⑤；非阻塞警告，不影响草稿回显）。

    只做静态可判项：必填缺失、顶层浅类型、enum、additionalProperties:false。
    无法静态判定的一律放行：未知工具/空 schema、params 非合法 JSON（含裸 ``{{}}``
    插值）、值为模板字符串（运行期插值）、字段声明 oneOf 等复合约束。
    """
    warnings: list[str] = []
    for raw_node in graph.get("nodes", []):
        if not isinstance(raw_node, dict) or raw_node.get("type") != "tool_call":
            continue
        node_id = raw_node.get("id", "?")
        config = raw_node.get("config")
        if not isinstance(config, dict):
            continue
        tool = config.get("tool")
        schema = tool_input_schemas.get(tool) if isinstance(tool, str) else None
        if not schema:
            continue
        properties = schema.get("properties")
        if not isinstance(properties, dict) or not properties:
            continue
        raw_params = config.get("params")
        if not isinstance(raw_params, str):
            continue
        try:
            params = json.loads(raw_params)
        except json.JSONDecodeError:
            logger.warning("nl_generate: 节点 %s 工具 %s 的参数 JSON 解析失败，跳过该节点", node_id, tool)
            continue
        if not isinstance(params, dict):
            continue
        prefix = f"节点「{node_id}」工具 {tool} "
        for required in schema.get("required", []):
            if required not in params:
                warnings.append(prefix + f"参数缺少必填字段：{required}")
        if schema.get("additionalProperties") is False:
            for key in params:
                if key not in properties:
                    warnings.append(prefix + f"参数包含未声明字段：{key}")
        for key, value in params.items():
            field_schema = properties.get(key)
            if not isinstance(field_schema, dict) or "oneOf" in field_schema:
                continue
            if isinstance(value, str) and "{{" in value:
                continue
            expected = field_schema.get("type")
            check = _TYPE_CHECKS.get(expected)
            if check is not None and not check(value):
                warnings.append(prefix + f"参数字段「{key}」类型应为 {expected}")
            enum = field_schema.get("enum")
            if isinstance(enum, list) and value not in enum:
                warnings.append(prefix + f"参数字段「{key}」取值不在允许范围内：{enum}")
    return warnings



def _generate_with_llm(
    prompt: str,
    model: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    repair: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    system = (
        "你是 Atlas 流程编排助手。把用户的中文需求转成 Graph JSON（只输出 JSON，不要解释）。"
        "结构：{\"version\":1,\"variables\":[{\"name\",\"type\",\"value\",\"scope\":\"global\"}],"
        "\"nodes\":[{\"id\",\"type\"(trigger/ai_decision/tool_call/condition/loop/parallel/wait/subgraph/human_approval),\"name\",\"description\","
        "\"position\":{\"x\",\"y\"},\"config\":{...},\"retry\":{\"max_retries\":0,"
        "\"backoff\":\"1s\",\"timeout\":30,\"on_error\":\"stop\"}}],\"edges\":[{\"id\",\"source\",\"target\"}]}。"
        "节点支持 trigger/ai_decision/tool_call/condition/loop/parallel/wait/subgraph/human_approval 九类，"
        "流程只能有一个 trigger 起点，不能把结束节点建成 trigger；"
        "trigger 的 config 为 {\"triggerType\":\"manual|schedule|webhook\",\"cron\",\"webhookUrl\"}，"
        "schedule 必须给 5 段 cron（如 \"0 9 * * *\"），webhook 必须给 webhookUrl，manual 可留空；"
        "ai_decision 的 config 必须带非空 promptTemplate（例如“判断退款查询结果是否成功”），"
        "缺少它会被 DSL 直接拒绝；能用确定性 condition 判断时不要硬塞 ai_decision。"
        "condition 节点的 config 为 {\"branches\":[{\"label\",\"expression\",\"target\"}],"
        "\"defaultTarget\"}：branches 按顺序短路，expression 仅支持 {{路径}} 变量引用、"
        "比较运算（> >= < <= == !=）、逻辑运算（&& || !）、括号与数字/字符串/true/false/null 字面量，"
        "禁止算术与函数；每个 branch 的 target 与 defaultTarget 都必须是已存在的节点 id，"
        "且每个目标都要有对应 edge，defaultTarget 必填。"
        "loop 节点（v1 仅条件循环）的 config 为 {\"mode\":\"while\",\"continueExpression\","
        "\"maxIterations\":10,\"bodyTarget\",\"exitTarget\"}：continueExpression 语法同 condition 表达式，"
        "为真时进入/再次进入循环体；maxIterations 为 1-100 的整数；bodyTarget 与 exitTarget 必须是已存在的节点 id；"
        "循环体末端节点必须连一条回到该 loop 节点的回边，exitTarget 另连一条出边，"
        "loop 节点恰好两条出边且不允许嵌套循环。"
        "parallel 节点（并行扇出/汇聚）的 config 为 {\"joinStrategy\",\"branches\":"
        "[{\"label\",\"target\"}],\"joinTarget\"}：joinStrategy 仅支持 all_success 或 all_completed；"
        "all_success 表示任一分支失败则汇聚状态 failed（汇聚节点仍执行），"
        "all_completed 表示各分支都走到汇聚即成功；branches 为 2-10 个，label 非空且不重复，"
        "target 必须是已存在且互不相同的节点 id；joinTarget 必须是已存在的节点 id 且不等于任一分支 target；"
        "parallel 节点的出边数恰好等于分支数且目标就是各 branch target，不直连结束；"
        "每个分支沿其内部连线最终必须能到达 joinTarget（分支末端连到 joinTarget），"
        "分支之间不得交叉连线，parallel 区域内不得再嵌套 parallel 节点。"
        "wait 节点（定时等待）的 config 为 {\"waitType\":\"duration\",\"durationSeconds\"}："
        "waitType 仅支持 duration（事件等待不支持），durationSeconds 为 1-600 的整数秒常量；"
        "wait 节点恰好配置一条出边，指向已存在的后继节点，不直连结束。"
        "subgraph 节点（引用已保存子图）的 config 为 {\"graphId\",\"inputs\":{\"入参键\":\"{{父图路径}} 或字面量\"}}："
        "graphId 为必填的已保存图 id（用户需先保存子图，不要凭空捏造 id）；inputs 可选，"
        "值用父图 {{路径}} 引用或字面量描述子图入参映射；subgraph 节点恰好配置一条出边，"
        "指向已存在的后继节点，不直连结束。"
        "human_approval 节点（人机协作审批）的 config 为 {\"summary\",\"approver\",\"timeoutSeconds\","
        "\"onTimeout\",\"approvedTarget\",\"rejectedTarget\"}：summary 为必填的审批说明，支持 {{路径}} 变量引用；"
        "approver 为可选审批人文案（仅展示，不鉴权）；timeoutSeconds 为 10-3600 的整数秒；"
        "onTimeout 仅支持 approve 或 reject（默认 reject，超时后 run 仍继续完成）；"
        "approvedTarget 与 rejectedTarget 必须是已存在、互异且不等于自身的节点 id；"
        "human_approval 节点恰好两条出边，目标分别就是 approvedTarget 与 rejectedTarget，不直连结束。"
        "tool_call 节点的工具必须从编辑器「可用工具」（GET /api/adapters）中选择，不要凭空捏造："
        "退款/售后场景优先使用 shop/* 已注册工具，禁止用 http/request 代替 shop 退款工具；"
        "执行退款用 shop/execute_refund 或 shop/process_refund，拒绝用 shop/reject_refund，"
        "审批用 shop/request_human_approval 或 human_approval 节点；通知一律用 message/send。"
        "通用 HTTP 请求用 http/request，config 为 {\"tool\":\"http/request\",\"params\":"
        "\"{\\\"method\\\":\\\"GET\\\",\\\"url\\\":\\\"/orders\\\",\\\"headers\\\":{...},"
        "\\\"body\\\":{...}}\"}——params 是 JSON 字符串，支持 {{路径}} 插值，"
        "任何 HTTP 响应（含 4xx/5xx）均为成功，由后续 condition 按 result.status 分支。"
        "通用 SQL 数据库用 database/query（只读、幂等）与 database/execute（写入、非幂等），"
        "config.params 为 JSON 字符串，形如 "
        "\"{\\\"sql\\\":\\\"SELECT order_id, amount FROM orders WHERE amount > :min\\\","
        "\\\"params\\\":{\\\"min\\\":1000},\\\"limit\\\":500}\"——值一律用绑定参数 params，"
        "不要把值拼进 sql 文本；query 返回 {columns,rows,row_count,truncated}，"
        "execute 返回 {rowcount}。"
        "进程内消息通知用 message/send（写入、非幂等），config.params 形如 "
        "\"{\\\"channel\\\":\\\"email\\\",\\\"to\\\":[\\\"ops@example.com\\\"],"
        "\\\"subject\\\":\\\"...\\\",\\\"body\\\":\\\"...\\\"}\"——to 为字符串或至多 20 个地址的数组，"
        "仅记录到 Demo 消息列表，无真实投递。"
        "店铺退款 Demo 的工具为 shop/login、shop/list_pending_refunds、shop/execute_refund、"
        "shop/request_human_approval、shop/process_refund；"
        "shop/list_pending_refunds 的产出为 {\"orders\":[{\"order_id\",\"amount\",...}]}，"
        "金额必须写 {{<节点id>.result.orders[0].amount}}，不要写 result.amount；"
        "shop/execute_refund 的产出为 {\"order_id\",\"status\"}；"
        "网页操作工具为 web-playwright/navigate（{url}）、click（{element_desc, selector?}）、"
        "type（{selector, text}）、screenshot（{}），config.params 同样是 JSON 字符串；"
        "长期记忆工具为 memory/remember（{kind, content}，kind 为 fact/preference/knowledge）、"
        "memory/recall（{query, top_k?}）、memory/auto_extract（{text}），params 为 JSON 字符串。"
        "硬性约束：nodes 中出现的 id 必须唯一；edges 的 source/target 必须都是 nodes 中真实存在的 id；"
        "不要生成未定义的 end/start 占位节点；condition 的每个 branch target 与 defaultTarget、"
        "human_approval 的 approvedTarget/rejectedTarget、loop/parallel 的 target 都必须有对应节点和 edge。"
        "condition 的 defaultTarget 必须与每个 branch target 不同；只有一个分支时，默认分支必须指向另一个节点。"
        "所有 {{路径}} 必须用真实节点 id，不能用节点类型名替代 id：trigger 产出写作 "
        "{{<trigger节点id>.context.payload.<key>}}（不是 {{trigger.payload...}}）；"
        "tool_call 产出统一包在 result 下，写作 {{<tool节点id>.result...}}"
        "（例如 database/query 的金额用 {{<节点id>.result.rows[0].amount}}，不要写 {{<节点id>.rows...}}）。"
        "全局变量必须写 {{global.<变量名>}}，不能写裸 {{<变量名>}}；"
        "例如 variables 里声明 customer_email，模板里只能写 {{global.customer_email}}。"
        "流程结束节点不再连出边，禁止把末端连回 trigger 形成环；需要结束时直接用最后节点，"
        "不要用 condition 模拟结束。退款失败/异常通知用 message/send，不要用 http/request 代替通知。"
    )
    if repair is not None:
        system += (
            "\n上一版草稿未通过编译校验，错误如下：\n- "
            + "\n- ".join(repair.get("errors", [])[:12])
            + "\n请只输出修正后的完整 Graph JSON，不要解释，不要复用上一版结构。"
        )
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "temperature": 0.2,
        # J-3c/A-06：出向 LLM 必须带超时与输出上限（长流程实测 950 tokens，1024 会截断）。
        "timeout": float(os.getenv("ATLAS_LLM_TIMEOUT_SECONDS", "60")),
        "max_tokens": _MAX_TOKENS,
    }
    # docs/93 打包 Y：BYOK/内置显式传 key/base_url；None 时不传，litellm 走 env。
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    # docs/129 打包 AU：唯一 litellm 出口（usage 计量旁路，不影响草稿生成语义）。
    content, _usage = complete_with_usage(kwargs, caller="nl_generate", model=model)
    graph = _parse_graph_json(content)
    if graph is None:
        logger.warning("nl_generate: LLM 返回的 JSON 提取失败，返回 None")
        return None
    graph["version"] = 1
    return graph


def _parse_graph_json(content: str) -> dict[str, Any] | None:
    """容忍代码围栏/尾部解释/trailing comma 的 JSON 解析；仍只接受含 nodes 的对象。"""
    if not isinstance(content, str) or not content.strip():
        return None
    candidates: list[str] = []
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", content, re.DOTALL | re.IGNORECASE):
        candidates.append(match.group(1).strip())
    candidates.append(content.strip())
    for candidate in candidates:
        start = candidate.find("{")
        texts = [candidate[start:]] if start >= 0 else []
        texts.append(candidate)
        for text in texts:
            for cleaned in (text, re.sub(r",\s*([}\]])", r"\1", text)):
                try:
                    value, _index = json.JSONDecoder().raw_decode(cleaned)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict) and ("nodes" in value or "edges" in value):
                    return value
    return None


def _collapse_constant_conditions(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """折叠“所有分支和默认目标都指向同一节点”的条件节点。

    这种 condition 没有决策作用，只剩一条固定路由。把它删除、把入边改指目标节点，
    是等价简化；若条件 id 被其他节点 config 引用，则不折叠，避免制造悬空引用。
    """
    changed = True
    while changed:
        changed = False
        node_ids = {str(node["id"]) for node in nodes}
        for node in list(nodes):
            if node.get("type") != "condition":
                continue
            node_id = str(node["id"])
            config = node.get("config")
            if not isinstance(config, dict):
                continue
            targets = {
                str(branch.get("target"))
                for branch in config.get("branches") or []
                if isinstance(branch, dict) and branch.get("target")
            }
            if config.get("defaultTarget"):
                targets.add(str(config["defaultTarget"]))
            if len(targets) != 1:
                continue
            target = next(iter(targets))
            if target == node_id or target not in node_ids:
                continue
            if any(
                node_id in json.dumps(other.get("config"), ensure_ascii=False)
                for other in nodes
                if other is not node
            ):
                continue
            # 删除 condition 节点，入边改指 target，出边全部丢弃
            nodes = [item for item in nodes if item is not node]
            new_edges: list[dict[str, Any]] = []
            seen: set[tuple[str, str]] = set()
            for edge in edges:
                if edge["source"] == node_id:
                    continue
                item = dict(edge)
                if item["target"] == node_id:
                    item["target"] = target
                key = (item["source"], item["target"])
                if key in seen:
                    continue
                seen.add(key)
                new_edges.append(item)
            edges = new_edges
            changed = True
            break
    return nodes, edges


_REF_PATTERN = re.compile(r"\{\{([^{}]+)\}\}")


def _qualify_global_refs(
    graph: dict[str, Any], nodes: list[dict[str, Any]], node_ids: set[str]
) -> None:
    """把裸 {{变量名}} 收敛为 {{global.变量名}}（变量已声明且不与节点 id 冲突）。

    模型经常把 global 变量写成裸名；这是无歧义修复：只改已声明变量、
    且该根名不是节点 id 的引用。未声明的引用保持原样，交给编译校验报错。
    """
    global_names = {
        str(item.get("name"))
        for item in graph.get("variables") or []
        if isinstance(item, dict) and item.get("name")
    }
    if not global_names:
        return

    def _walk(value: Any) -> Any:
        if isinstance(value, str):

            def _replace(match: re.Match[str]) -> str:
                path = match.group(1).strip()
                root = path.split(".", 1)[0]
                if (
                    root in global_names
                    and root not in node_ids
                    and not path.startswith("global.")
                ):
                    return "{{global." + path + "}}"
                return match.group(0)

            return _REF_PATTERN.sub(_replace, value)
        if isinstance(value, dict):
            return {key: _walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_walk(item) for item in value]
        return value

    for node in nodes:
        node["config"] = _walk(node.get("config"))


_ALLOWED_ON_ERROR = {"stop", "continue", "jump_to"}


def _normalize_retry(node: dict[str, Any]) -> None:
    """把模型自造的 retry.on_error 收敛到 DSL 枚举。

    `retry` 是模型最容易臆造的词（如 on_error="retry"）。只改非法枚举为最保守的
    `stop`，保留 max_retries/timeout 等原值；不静默改成 continue。
    """
    retry = node.get("retry")
    if retry is None:
        return
    if not isinstance(retry, dict):
        node.pop("retry", None)
        return
    if retry.get("on_error") not in _ALLOWED_ON_ERROR:
        retry["on_error"] = "stop"


def normalize_generated_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """把 LLM 草稿收敛到前端/DSL 可解析的最小形状。

    只做无歧义修复：补 version/default name、节点 id 去重、补默认坐标、
    丢弃两端不存在的 edge。配置里的分支目标不猜，仍交给 L2/L3 报错。
    """
    if not isinstance(graph, dict):
        return graph
    graph["version"] = 1
    raw_nodes = graph.get("nodes")
    nodes: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    if isinstance(raw_nodes, list):
        for index, raw_node in enumerate(raw_nodes):
            if not isinstance(raw_node, dict):
                continue
            node = dict(raw_node)
            raw_id = str(node.get("id") or f"node-{index + 1}").strip() or f"node-{index + 1}"
            node_id = raw_id
            suffix = 2
            while node_id in used_ids:
                node_id = f"{raw_id}-{suffix}"
                suffix += 1
            used_ids.add(node_id)
            node["id"] = node_id
            node["type"] = str(node.get("type") or "trigger")
            node["name"] = str(node.get("name") or node_id)
            if not isinstance(node.get("config"), dict):
                node["config"] = {}
            _normalize_retry(node)
            if not isinstance(node.get("position"), dict):
                node["position"] = {"x": (index % 5) * 180, "y": (index // 5) * 140}
            nodes.append(node)
    graph["nodes"] = nodes

    node_ids = {str(node["id"]) for node in nodes}
    node_types = {str(node["id"]): str(node.get("type") or "") for node in nodes}
    raw_edges = graph.get("edges")
    edges: list[dict[str, Any]] = []
    edge_ids: set[str] = set()
    if isinstance(raw_edges, list):
        for index, raw_edge in enumerate(raw_edges):
            if not isinstance(raw_edge, dict):
                continue
            source = str(raw_edge.get("source") or "").strip()
            target = str(raw_edge.get("target") or "").strip()
            if source not in node_ids or target not in node_ids:
                continue
            if source == target and node_types.get(source) != "loop":
                # 非 loop 节点的自环没有合法语义，直接丢弃；loop 的回边允许指向自身
                continue
            edge_id = str(raw_edge.get("id") or f"e{index + 1}").strip() or f"e{index + 1}"
            base = edge_id
            suffix = 2
            while edge_id in edge_ids:
                edge_id = f"{base}-{suffix}"
                suffix += 1
            edge_ids.add(edge_id)
            edges.append({"id": edge_id, "source": source, "target": target})
    graph["edges"] = edges
    nodes, edges = _collapse_constant_conditions(nodes, edges)
    graph["nodes"] = nodes
    graph["edges"] = edges
    node_ids = {str(node["id"]) for node in nodes}
    _qualify_global_refs(graph, nodes, node_ids)
    # condition 的 defaultTarget 与 branch target 撞车时，若出边里还有另一个目标，
    # 用它修默认分支；没有第二个目标就不猜，留给 L1/L2 报错。
    outgoing: dict[str, set[str]] = {}
    for edge in edges:
        outgoing.setdefault(edge["source"], set()).add(edge["target"])
    for node in nodes:
        if node.get("type") != "condition":
            continue
        config = node.get("config")
        if not isinstance(config, dict):
            continue
        branches = config.get("branches")
        branch_targets = {
            str(branch.get("target"))
            for branch in branches or []
            if isinstance(branch, dict) and branch.get("target")
        }
        default_target = config.get("defaultTarget")
        if default_target in branch_targets and isinstance(branches, list):
            # 与默认分支同目标的显式 branch 是冗余的：保留默认路径，删掉重复 branch。
            remaining = [
                branch
                for branch in branches
                if isinstance(branch, dict) and str(branch.get("target")) != str(default_target)
            ]
            if remaining:
                config["branches"] = remaining
        if default_target in branch_targets:
            alternatives = [
                target
                for target in outgoing.get(str(node.get("id")), set())
                if target not in branch_targets
            ]
            if alternatives:
                config["defaultTarget"] = sorted(alternatives)[0]
    if not isinstance(graph.get("variables"), list):
        graph["variables"] = []
    return graph


def validate_generated_graph(
    graph: dict[str, Any], registry: AdapterRegistry
) -> dict[str, Any]:
    """返回 L2/L3 编译反馈；不抛异常，供 API 与评估脚本消费。"""
    from atlas.graph.dsl import parse_graph, validate_graph_report
    from atlas.graph.loader import tool_output_schemas

    try:
        parsed = parse_graph(graph)
    except Exception as exc:  # noqa: BLE001 - pydantic ValidationError 只在边界转成报告
        return {"ok": False, "errors": [str(exc)], "codes": ["GRAPH_PARSE_ERROR"]}
    messages, _locations, codes, _params = validate_graph_report(
        parsed,
        tool_output_schemas(registry),
        check_refs=True,
    )
    return {"ok": not messages, "errors": messages, "codes": codes}
