# 打包 A1：模板库产品化收尾——版本/CAS＋搜索与使用统计＋参数化向导（D25 收尾，v1 批契约设计）

> 状态：**形状权威**（2026-10-07 立项，用户「那你推进」批准；承接 docs/14 D25 剩余三件）
> 前序：D25 已取回「PUT 更新」（打包 Y，docs/86）与「分类管理＋URL 导入导出」（打包 ZM）。本批取回**剩余三件**：版本钉版基线（version/updated_at＋CAS 防覆盖）、搜索与使用统计、参数化向导（填空式实例化）。

## 0. 前提（实测）

- 用户模板两档 store：`template/user_store.py UserTemplateStore`（内存）与 `template/pg_store.py PgUserTemplateStore`（PG），方法面逐字一致；字段 `id/name/description/tags/category/graph/created_at`，id 空间 `utpl-<租户内 seq>`。
- 建表迁移 **034_user_templates.sql**（打包 X）＋ **036_user_template_category.sql**（打包 ZM，category 列）。迁移最大序号 **042**（password_rotation）⇒ 本批新迁移 **043**。
- REST（api/main.py:3041–3300）：`GET /api/templates`（内置目录在前＋用户模板在后，投影不含 graph）、`GET /api/templates/{id}`（详情含 graph）、`POST /api/templates`（operate，另存为）、`PUT /api/templates/{id}`（operate，整体替换，tags 缺省保留旧值）、`DELETE /api/templates/{id}`（operate）、`GET /api/templates/{id}/export`、`POST /api/templates/import`（包/URL 双通道）。
- Graph JSON `variables` 区：`list[GraphVariable{name,type,value,scope:"global"}]`（dsl.py:136-140）；运行时 `_seed_variables` 生成 `{"global":{name:value}}`，run inputs overrides 合并进 global（loader.py:350-405）。**参数化实例化＝把表单值覆写进 `graph.variables`**，语义与运行期变量供给同源。
- 前端：apiClient `TemplateSummary/TemplateDetail`（apiClient.ts:645-669）＋`listTemplates/getTemplate/createUserTemplate/updateUserTemplate/deleteUserTemplate/exportTemplate/importTemplate`；「从模板新建」在 `pages/Editor.tsx`（整画布替换，loadGraph）。
- 最新验收号段 U1179（打包 BL）⇒ 本批 **U1180–U1188**。前端 vitest 基线 789/2、oxlint 0/0、`pnpm build`（=tsc -b + vite build）为真类型门。

## 1. 范围 / 非目标

**取回三件（全部工程内可闭环、零外部依赖）**：

1. **模板版本与 CAS 防覆盖**：`UserTemplate` 增 `version:int=1`、`updated_at:str`；`PUT` 请求体可选 `if_match_version`——匹配才更新（否则 **409 `TEMPLATE_VERSION_CONFLICT`**）；update 递增 version 并刷新 updated_at；列表/详情投影带 version/updated_at。
2. **搜索 + 使用统计**：`GET /api/templates?q=`（read；内置＋用户两段都过滤：name/description/tags/category 任一命中，大小写不敏感子串；q 缺失/空白/空串＝全量）；用户模板投影增 `usage_count:int`；新端点 `POST /api/templates/{id}/usage`（**operate**）计数 +1（仅用户模板；内置/不存在/跨租户 404）。
3. **参数化向导**：`UserTemplate` 增 `params:dict`（缺省 `{}`，JSON Schema 子集：`{"<name>": {"type":"string|number|boolean|select","label","required","default","hint","options":[..]}}`）；POST/PUT 可携带（服务端形状校验）；详情投影带 params；新端点 `POST /api/templates/{id}/instantiate`（operate）body `{values:{<name>:any}}` → 校验（required/类型/options）→ 返回 `{graph}`：模板图**深拷贝**后把 values 覆写进 `graph.variables`（命中 name 覆写 value，未命中 append `{name,type,value,scope:"global"}`），**模板原图不变**。

**非目标（明确缓做/不取回，docs/14 D25 其余保持）**：
- 模板市场/组织内共享/商业化抽成（D3、D25 市场半边）：触发条件＝多租户真实使用，未满足。
- 评分（用户打分 1-5）：触发条件＝真实用户反馈；v1 不造评分体系。
- 一键升级回归的**引用侧**（图引用模板版本、升级后批量回放门禁）：与 D21 子图钉版/升级回归同批，随其触发条件。
- 内置模板（catalog 常量）的 params 声明与 usage 计数：v1 仅用户模板（内置投影 usage_count 恒 0、params 恒空；前端对内置段不渲染使用次数/参数向导入口）。
- 模板搜索排序/分页；多语言模板描述（D13）。
- 短信/IM 应用 OAuth/入站消费（D24 其余，触发条件未满足）。

## 2. 决策（E 组）

- **E-1 版本语义＝乐观锁 CAS，不做历史版本归档**：version 仅用于并发覆盖防护与变更可查（updated_at 佐证），不保留历史快照；「模板版本历史/回滚」登记缓做（触发＝真实多人编辑冲突）。v1 不引 PATCH/单字段更新。
- **E-2 CAS 形状＝请求体可选 `if_match_version`**：缺省＝无防护（兼容既有 PUT 调用，行为逐字不变）；不匹配→409 `TEMPLATE_VERSION_CONFLICT`（结构化 detail，前端可 i18n）。不用 `If-Match` 头（前端 fetch 头拼接与现有错误处理约定不齐，请求体更直白且可被 apiClient 类型覆盖）。
- **E-3 搜索语义＝两段统一子串过滤**：内置（本地化 name/description 后过滤）＋用户（原始字段）各自 `q` 过滤后拼接，保持"内置在前、用户在后"现有顺序；无排序/分页。
- **E-4 使用统计＝显式 touch 端点**：前端在「从模板新建」成功实例化后调用 `POST /api/templates/{id}/usage`；v1 不自动埋点（图运行不反查模板来源，模板与运行无绑定关系，诚实不臆造归因）。计数列级（迁移 043 加 `usage_count BIGINT NOT NULL DEFAULT 0`），内存档同字段。
- **E-5 params 声明＝服务端权威校验、前端表单生成**：params 形状服务端白名单校验（未知 key/类型非法/options 非法 → 422）；前端仅按声明渲染表单（type→控件、required 必填、default 预填、options→Select、hint 提示），不做客户端 schema 引擎（D29 不触碰）。
- **E-6 instantiate 语义＝变量覆写（variables 区）**：values 只写 `graph.variables`（运行期变量供给），不替换图内字面量、不改节点配置——模板图内 `{{<name>}}` 引用经变量区生效；number 严格接受 int/float（字符串数字 422，避免隐式转换歧义）、boolean 接受 true/false、select 值必须在 options、string 任意非空？——string 允许空串（required=false 时）。
- **E-7 权限与租户**：usage/instantiate 均 `require("operate")`；内置模板 404（v1 内置无参数化/计数，诚实 404 不假装支持）；跨租户 404 不泄漏存在性（照既有约定）。

## 3. 契约形状

### 3.1 迁移 043_user_template_meta.sql（新文件，照 034/036 样板：首非空行注释、IF NOT EXISTS 幂等、不改 002）

```sql
-- 打包 A1：模板库产品化收尾——版本/CAS＋使用统计（docs/97）。
-- user_templates 加 version（乐观锁，PUT 递增）、updated_at（变更时间）、usage_count（显式 touch 计数）；
-- 存量行回填 version=1、updated_at=created_at、usage_count=0。
ALTER TABLE user_templates ADD COLUMN IF NOT EXISTS version BIGINT NOT NULL DEFAULT 1;
ALTER TABLE user_templates ADD COLUMN IF NOT EXISTS updated_at TEXT NOT NULL DEFAULT '';
ALTER TABLE user_templates ADD COLUMN IF NOT EXISTS usage_count BIGINT NOT NULL DEFAULT 0;
UPDATE user_templates SET updated_at = created_at WHERE updated_at = '';
COMMENT ON COLUMN user_templates.version IS '模板版本号（乐观锁 CAS；PUT 成功递增 1，缺省 1）';
COMMENT ON COLUMN user_templates.updated_at IS '最近变更 UTC ISO（add=created_at；PUT 刷新）';
COMMENT ON COLUMN user_templates.usage_count IS '从模板新建实例化计数（POST /usage 显式 +1，v1 不自动埋点）';
```

### 3.2 模型增量（template/user_store.py ＋ template/pg_store.py 同步）

- `UserTemplate` 增：`version:int=1`、`updated_at:str=""`、`usage_count:int=0`、`params:dict[str,Any]=Field(default_factory=dict)`。
- `UserTemplateRepository` Protocol 方法签名同步（add/update 增 `params` 形参；update 增 `if_match_version: int|None`）。
- **内存档**：`add` 置 version=1、updated_at=created_at、usage_count=0；`update` 若 `if_match_version` 非 None 且 ≠ 当前 version → 返回 `None` 并置标志？——**CAS 冲突与"不存在"必须可区分**：内存档返回类型保持 `UserTemplate|None`，冲突时抛 `TemplateVersionConflict`（自定义异常，api 层 409）；不存在仍 `None`→404。`update` 成功：version+1、updated_at=now。`touch(template_id)->bool`（存在则 usage_count+1 返 True）。`get/list` 原样带新字段。
- **PG 档**：同语义（UPDATE ... WHERE tenant_id/id 且 version=:if_match 返回 rowcount 判冲突；usage_count 原子 `UPDATE ... SET usage_count=usage_count+1`）。

### 3.3 REST（api/main.py）

- `GET /api/templates` 增可选 query `q: str|None`；内置段先 `localize_template` 再按 q 过滤（name/description/tags/category 任一 `casefold` 子串命中）；用户段同样过滤；两段投影各增 `version`、`updated_at`（用户段）、`usage_count`（用户段）、`params: {}`（列表不含 params 内容？——列表投影**不带** params（保持不含 graph 的轻投影），详情带）。
- `PUT /api/templates/{id}` 请求体增可选 `if_match_version:int|None`、`params:dict|None`（None＝保留旧值，照 tags 先例）；成功返回更新投影（含 version/updated_at/usage_count/params）。
- `POST /api/templates/{id}/usage`（operate）：用户模板存在→touch 返 `{"usage_count":N}`；内置/不存在/跨租户→404。
- `POST /api/templates/{id}/instantiate`（operate）：body `{values:dict[str,Any]}`（值校验见 E-6）；成功 `{"graph": <深拷贝+变量覆写>, "template": {id,name,version}}`；422 中文 detail（参数名/原因）；404 规则同上。
- `params` 形状校验（POST/PUT 共用纯函数 `validate_params(params)->list[str]` 错误）：type ∈ {string,number,boolean,select}；label ≤40；required bool；select 必须 `options` 非空 list[str] ≤20；未知键拒绝。非法→422（聚合中文 detail，照 parse_graph 先例）。

### 3.4 前端形状

- apiClient：`TemplateSummary` 增 `version?/updated_at?/usage_count?`（用户段）；`TemplateDetail` 增 `version/updated_at/usage_count/params`；`listTemplates(q?:string)`；新 `touchTemplateUsage(id)`、`instantiateTemplate(id, values)`；类型 `TemplateParam = {type,label,required,default?,hint?,options?}`。
- Editor「从模板新建」Modal：列表加「使用次数」列（内置段显示 `—`）；详情渲染时若 `params` 非空→提交前先弹参数表单（按声明生成控件），确定后 `instantiateTemplate` 拿 graph → 既有整画布替换；随后 `touchTemplateUsage`（成功后刷新列表计数）。
- i18n：模板相关新文案（版本/使用次数/参数向导/必填/冲突提示）进既有模板 ns 或 editor ns（zh/en 双档、PARITY 守护）。

### 3.5 验收（docs/13 号段 U1180–U1188）

- **U1180**（后端）：add 后 version=1/updated_at=created_at/usage_count=0；update 成功 version+1 且 updated_at 刷新；列表/详情投影带三字段＋params。
- **U1181**（后端）：CAS——`if_match_version` 匹配→200；不匹配→409 `TEMPLATE_VERSION_CONFLICT`；缺省→照旧 200 无防护；不存在/内置 id→404（先例：PUT 已 404）。
- **U1182**（后端）：q 搜索——内置与用户两段均过滤（name/tags/category/description 命中）、大小写不敏感、q 缺失/空白→全量、无命中→空 items。
- **U1183**（后端）：usage 计数——用户模板 touch 后列表 usage_count 累加；内置 404；不存在/跨租户 404；PG 档同语义（集成标记）。
- **U1184**（后端）：params 形状校验——合法声明通过；type 非法/select 无 options/label 超长/未知键→422 且原模板未变。
- **U1185**（后端）：instantiate——required 缺失 422；number 字符串/非数字 422；boolean 非布尔 422；select 值不在 options 422；成功返回深拷贝图。
- **U1186**（后端）：instantiate 变量语义——命中 name 覆写 value；未命中 append `{name,type,value,scope:"global"}`；模板原图逐键不变（深拷贝证据）；运行 `_seed_variables` 后 global 含表单值（语义贯通证明）。
- **U1187**（前端 vitest）：apiClient 形状——listTemplates(q) 透传；touch/instantiate 方法与请求路径/方法；TemplateSummary/Detail 新字段类型。
- **U1188**（前端 vitest）：参数表单纯逻辑——params 声明→字段映射（type→控件类型/required/default/options）；提交值组装 instantiate body。

## 4. 同步面（改内容流程）

docs/97（本文，形状权威）＋docs/00 地图（登记 97）＋docs/08（打包 A1 立项/收口块）＋docs/13（U1180–U1188 预告/收口）＋docs/14（D25 注记：三件取回、市场/评分/升级引用侧仍缓做）＋docs/03（user_templates 契约注记）＋docs/12（新端点两行）＋CHANGELOG＋handoff（顶部流水/Active/Recently shipped/Quality gate）。

## 5. 原子序（docs/94 §5 同款八步）

① docs 立项（本文＋同步面，docs-only）→ ② feat(db) 迁移 043＋两档 store → ③ feat(api) q/usage/instantiate/CAS/params 校验 → ④ test 后端 U1180–U1186 → ⑤ feat(frontend) → ⑥ test 前端 U1187–U1188 → ⑦ docs 收口。**不 push**（用户未授权）；作者 bayernjf、无 AI co-author。

## 6. 缓做登记（D25 剩余与后续）

- 模板版本历史/回滚、PATCH 单字段更新：触发＝真实多人编辑冲突。
- 模板市场/组织内共享/商业化（D3 同源）：触发＝多租户真实使用。
- 评分/使用统计自动埋点（图运行→模板归因）：触发＝真实使用反馈；v1 用显式 touch。
- 一键升级回归的引用侧（D21 子图治理同批）：触发条件不变。
- 内置模板 params 声明/计数、搜索排序分页、多语言描述（D13）：随需另立。
