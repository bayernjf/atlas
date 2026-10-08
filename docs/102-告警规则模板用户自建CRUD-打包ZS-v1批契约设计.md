# 打包 ZS 告警规则模板用户自建 CRUD 与租户市场 v1 批契约设计

> 承接：14 **D28** 余部「模板 CRUD/市场上传」（F 批只做内置只读目录＋一键应用，写通道为零）。
> 形状权威：docs/59（F-1 `RuleTemplateMeta`／内置只读市场）、docs/85 打包 X（`user_templates` 两档 store＋迁移 034＋registry 装配＋reset 同清）、docs/98 打包 A2（消息模板 CRUD 错误码先例）。
> 定稿：2026-10-08。用户授权「搞你能搞的」自主推进；候选池复筛结论＝D28 规则模板 CRUD 是登记在案、工程内可闭环的干净余部（打包 X/A1 已证明「模板 CRUD 在单租户 Demo 可演」）。

## §1 背景与目标

**背景**：docs/59 F-1 已落「内置只读告警规则模板市场」——4 个随代码发布的模板（default-balanced／strict-sre／demo-lenient…），`GET /api/alert-rule-templates` 两只读端点，前端 `AlertRuleTemplateMarket` 列表＋「一键应用」（取 config 走 `PUT /api/monitoring/rules` 全量替换，administer）。**用户无任何写通道**：沉淀自有告警规则只能手写 RuleConfig JSON 到规则表单，或改代码。

**目标**：取回「模板 CRUD／市场上传」最小切片——**用户自建／编辑／删除私有规则模板**，列表与内置目录合并（source 区分），「一键应用」对用户模板同样生效。单租户 Demo 下「列表合并＋可一键应用」即租户内市场；跨租户市场／抽成明确不做（D3／D25 同族，触发未满足）。

**不做**：规则配置 UI 表单化（v1 走 config JSON 文本编辑＋既有 `validate_rules` 校验）；模板版本历史/回滚、评分、多语言描述、按图绑定、发布门禁联动。

## §2 现状（实测，2026-10-08）

| 位置 | 现状 |
|---|---|
| `src/atlas/monitoring/rule_templates.py` | `RuleTemplateMeta`（id/name/description/tags/config），`RULE_TEMPLATES` 元组 4 内置，纯只读常量目录；模块 docstring 明言「无 DB、无 CRUD、不按租户分区、不受 /api/demo/reset 影响」 |
| `src/atlas/api/main.py:3626/3644` | `GET /api/alert-rule-templates`（列表）、`GET /api/alert-rule-templates/{template_id}`（详情） |
| `frontend/src/components/monitoring/AlertRuleTemplateMarket.tsx` | 只读列表＋一键应用（`getAlertRuleTemplates`/`getAlertRuleTemplate`/`updateRules`，Popconfirm 二次确认）；`Monitoring.tsx:619` 挂载 |
| 先例（打包 X） | `template/user_store.py`（`UserTemplate`＋`UserTemplateRepository` Protocol＋内存 store）＋`template/pg_store.py`（`PgUserTemplateStore` 方法面逐字一致）＋迁移 034 `user_templates`＋registry `:91/128/168/199` 两档装配＋`:234` `services.user_templates.clear()`（reset 同清） |
| 先例（打包 A2） | `message/template_store.py:39`：租户内重名 409 `MESSAGE_TEMPLATE_NAME_CONFLICT`；docs/03:1314 错误码取值集合登记机制 |
| 校验 | `src/atlas/monitoring/alerts.py:98` `validate_rules(raw) -> list[str]`（错误列表；空列表＝通过）——`PUT /api/monitoring/rules` 已复用 |

## §3 决策

### §3.1 实体

```python
class UserRuleTemplate(BaseModel):
    id: str            # urt-{seq}（全局 storage_id_seq 取数，与 rr-/sr-/utpl- 同族）
    name: str          # 租户内唯一，1-100
    description: str
    tags: list[str]
    config: dict[str, Any]   # 完整 RuleConfig dict（四段内置规则齐备）
    created_at: str    # UTC ISO-8601
```

### §3.2 存储（照打包 X 两档同构）

- 新模块 `src/atlas/monitoring/rule_user_store.py`：`UserRuleTemplate`＋`UserRuleTemplateRepository`（Protocol）＋`UserRuleTemplateStore`（内存 list，id 递增计数器）。
- 新模块 `src/atlas/monitoring/pg_rule_user_store.py`：`PgUserRuleTemplateStore`，方法与内存档**逐字一致**（add/get/list/update/delete/clear），id 取 `nextval('storage_id_seq')` 生成 `urt-{n}`、数字部分另存 seq 列（照 `pg_reports.py` 先例），tags/config JSONB 用 `json.dumps` 写、`model_validate` 读。
- 迁移 `045_user_rule_templates.sql`：

```sql
CREATE TABLE IF NOT EXISTS user_rule_templates (
    tenant_id TEXT NOT NULL,
    id TEXT NOT NULL,
    seq BIGINT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    config JSONB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_user_rule_templates_tenant_seq
    ON user_rule_templates (tenant_id, seq);
```

- 装配：`TenantServices` 加 `rule_template_store: UserRuleTemplateRepository`；registry PG 档注入 `PgUserRuleTemplateStore(backend.engine, tenant_id)`、内存档 `UserRuleTemplateStore()`；reset 清本租户（照 `user_templates` 加 `services.rule_template_store.clear()`）。

### §3.3 校验（复用既有通道）

- `config` 必须通过 `alerts.validate_rules`：错误列表非空 → **422 `RULE_TEMPLATE_CONFIG_INVALID`**（detail 中文聚合，照 MESSAGE_TEMPLATE 先例的端点内手工中文）。
- `name` 租户内唯一：重复 → **409 `RULE_TEMPLATE_NAME_CONFLICT`**（PUT 改名撞名同 409；改名后与自身同名不算冲突）。
- `name` 空/超长：pydantic 422（Field min_length=1/max_length=100，照 `RecordingCreateRequest` 先例）。
- 两个新错误码登记进 docs/03 取值集合与 docs/17 文案目录（守护 `tests/test_error_code_channels.py` 随落码原子同步）。

### §3.4 REST 端点（权限：GET read；写 administer，与 `PUT /api/monitoring/rules` 对齐）

| 端点 | 语义 |
|---|---|
| `GET /api/alert-rule-templates`（扩展现有） | 内置＋用户合并；现有投影为**纯超集**（summary 增 `source: "builtin"\|"user"`），既有前端零破坏 |
| `GET /api/alert-rule-templates/{template_id}`（扩展现有） | 内置直接返（source=builtin）；用户按租户查，不存在/跨租户 **404**（照 release-reports 跨租户 404 防泄漏口径） |
| `POST /api/alert-rule-templates` | 自建（201，body＝{name,description?,tags?,config}，过 §3.3 校验） |
| `PUT /api/alert-rule-templates/{template_id}` | 编辑 name/description/tags/config（整体替换，id/seq/created_at 不变，照 docs/86 打包 Y 的 PUT 语义）；内置 id **404**（不可改）；不存在 404 |
| `DELETE /api/alert-rule-templates/{template_id}` | 仅用户模板；内置 id **404**（不可删）；不存在 404 |

内置 id（`default-balanced` 等语义名）与用户 id（`urt-N`）天然不冲突；写端点按「id 以 `urt-` 前缀开头且存在」判定可改删，否则 404。

### §3.5 前端（`AlertRuleTemplateMarket` 扩展）

- 列表：source Tag（内置「内置」/ 用户「自建」），用户模板行加「编辑」「删除」操作（Popconfirm）。
- 「新建模板」入口（administer 显示，照 DemoResetButton 的 `roleCan` 模式）：Modal 表单＝name / description / tags（输入框逗号分隔）／config（JSON TextArea，提交前前端 `JSON.parse` 预校验，失败就地报错不发请求——照 paramWizard 的「非法就地报错」先例）。
- 编辑复用同一 Modal（预填现值）；「一键应用」对两类模板同样生效（现有逻辑不动）。
- i18n：`monitoring` namespace 加 zh-CN/en-US 键（新建/编辑/删除/确认删除/内置/自建/JSON 非法等）；PARITY 守护自动覆盖新键。

### §3.6 验收

- 后端（tests/）：内存档 CRUD 全链＋校验拒绝（坏 config 422、重名 409、改名撞名 409、改名自身不冲突）＋内置保护（PUT/DELETE 内置 404）＋跨租户 404＋reset 清空；PG 集成（一次性 pgvector 容器 5445）两档对拍。
- 前端（vitest）：Market 组件新建/编辑/删除交互、JSON 非法就地报错、source Tag。
- 三道门：后端全量 `.venv/bin/pytest`、守护门三件套、前端 vitest＋oxlint＋build。

## §4 落码原子序

1. **docs-only 立项**：docs/102（本文）＋docs/08 立项块＋docs/14 D28 注记＋docs/03 错误码集合＋docs/00 地图行＋handoff Active 新行＋CHANGELOG 立项条。
2. **feat(monitoring)**：迁移 045＋`rule_user_store.py`＋`pg_rule_user_store.py`＋registry 装配（含 reset clear）。
3. **feat(api)**：五端点（GET 两处扩展＋POST/PUT/DELETE）＋§3.3 校验＋错误码。
4. **test(api)**：§3.6 后端用例（U1220 起编号，以 docs/13 收口实编为准）。
5. **feat(frontend)**：apiClient 三方法＋Market CRUD＋source Tag＋i18n 两档。
6. **test(frontend)**：vitest。
7. **docs 收口**：三道门实跑数字回填（docs/102 §7、docs/13、docs/08 收口块）＋handoff 划销＋CHANGELOG 收口条。

## §5 边界与不做

- 跨租户模板市场／组织内共享／商业化抽成（D3／D25 同源，触发未满足）。
- 模板版本历史／回滚／PATCH／CAS（D25）。
- 模板多语言描述（D13）。
- 规则配置 UI 表单化（v1 保持 config JSON 文本＋validate_rules）。
- 按图绑定模板、发布门禁联动、模板使用统计。
- D28 其余余部（OTel 正式栈、静默编辑、多值班组／排班日历、多实例分布式锁、长保留时序报表）——触发条件不变。

## §6 收口注记（落码后回填）
