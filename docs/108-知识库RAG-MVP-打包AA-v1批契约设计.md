# docs/108 · 知识库 / RAG MVP 批契约（打包 AA）

> **元信息**
> - 日期：2026-10-09
> - 承接：docs/107 §五 A1（需求文档「编辑后台组件设计 §7 知识组件」整块缺失，比对发现无既有 D 号）
> - 立项依据：用户「好的，按你建议」批复（docs/107 §六 第一优先）
> - docs/14 新 D 号：**D60**（本条不解除：跨运营体共享、多格式解析、知识更新审核流、重排仍缓做）
> - 形状权威：本文件；docs/14 D60 行注记；docs/13 打包 AA 小节
> - 验收：U1259 起

## 一、范围（MVP 切片）

**背景**：`memory_items` 统一表已服务 fact/preference（M11，迁移 006，vector(256) + ivfflat + LocalDeterministicEmbedder 离线确定性向量）。「知识」与「记忆」边界（编辑后台组件设计 §2.5）：知识＝用户主动上传/配置、可跨运营体共享；记忆＝运营体自动积累、默认绑定运营体。**MVP 不拆表**（跨运营体共享触发时再评估拆表），以 `kind='knowledge'` 扩展统一表，`meta.category` 存知识子类。

**本批取回**：
1. **存储扩展**：迁移 **046** `memory_items.kind` CHECK 扩至 `('fact','preference','knowledge')`；`meta.category` 子类白名单 `faq / sop / manual / rule / case`（FAQ 知识库、SOP 文档、产品手册、业务规则、历史案例，照编辑后台组件设计 §7.2）
2. **文档导入**：`POST /api/knowledge/import`（operate，body `{category, text, scope?}`）——纯文本按段落/最大长度分段，每段一条 `kind=knowledge` 条目向量化入库；响应 `{imported: N, items: [...]}`。**MVP 仅纯文本**，PDF/Word/网页解析后续
3. **知识 recall**：`memory/recall` 工具扩展 `kind='knowledge'` + 可选 `category` 过滤（`MEMORY_*` 错误码沿用；`adapter.py` 入参 schema 加 `category` 可选枚举）
4. **REST**：复用 `/api/memories` 既有端点（`list(kind=)` 已支持任意 kind；`create/update` 需放行 `kind='knowledge'` + category 校验；`search` 已支持 kind）——**零新端点**（除 import）
5. **前端**：记忆页改造为「记忆 / 知识」双标签；知识标签＝category 筛选 + 导入入口（TextArea + category 选择 + 导入按钮）+ 列表（复用 Memory 卡片形态）

**非目标（D60 不解除）**：多格式文档解析（PDF/Word/网页/URL）、跨运营体知识共享、知识更新审核流、RAG 重排/多路召回、商业 embedding provider、知识市场、知识版本化、`memory` 与 `knowledge` 拆表。

## 二、契约形状

### 2.1 数据模型（扩展 MemoryItem）

```
kind: fact | preference | knowledge        # 迁移 046 放行 knowledge
meta.category: faq | sop | manual | rule | case   # 仅 kind=knowledge 时可选；其他 kind 忽略
content: str（1–2000，分段后单条）          # 既有上限复用
scope: dict[str,str]                       # 既有语义（租户隔离语句级，无 RLS）
embedding: vector(256)                     # LocalDeterministicEmbedder，离线确定
```

### 2.2 端点

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | /api/knowledge/import | operate | body `{category, text, scope?}`；分段（段落优先，单段 >1200 字按字符切）；text 空/超限 422；category 非白名单 422；成功 201 `{imported, items}` |
| GET | /api/memories?kind=knowledge | read | 既有；前端知识标签用 |
| GET | /api/memories/search?kind=knowledge&category=... | read | 既有 + category 过滤（recall 层） |
| POST/PUT/DELETE | /api/memories... | operate/admin | 既有；kind=knowledge 时校验 category |

### 2.3 recall 工具

```
memory/recall 入参扩展：kind: fact|preference|knowledge（既有）+ category?: faq|sop|manual|rule|case
语义：kind=knowledge 且 category 给定 → 先 category 精确过滤再向量排序（SQL 层 WHERE kind+meta->>category，内存层同序）
```

### 2.4 导入分段规则

- 按段落（`\n\n`）切；单段 ≤1200 字符整段入库；超长段按 1200 字符边界硬切
- 空段跳过；总条数 >200 截断并响应 `{imported: N, truncated: true}`（200 = LIST_LIMIT_MAX 先例）

## 三、验收（U1259 起）

- U1259 迁移 046 后 kind=knowledge 可写可读、fact/preference 不受影响（PG 直连）
- U1260 import 纯文本分段：两段文本 → imported=2、两条目 meta.category 正确、embedding 非空
- U1261 import 超长段硬切、空段跳过、text 空 422、category 非法 422
- U1262 import 超 200 条截断响应 truncated
- U1263 recall kind=knowledge 向量排序命中（确定性：同 query 同结果）
- U1264 recall category 过滤（faq 只回 faq）
- U1265 内存档与 PG 档两档对拍（import 后两档 search 结果一致）
- U1266 REST create kind=knowledge 带合法 category 201、非法 category 422
- U1267 前端知识标签：导入 → 列表刷新 → category 筛选（组件测试）
- U1268 守护门 test_handoff_integrity + test_migration_convention 过

## 四、原子序与同步矩阵

```
docs 立项（本文件 + docs/14 D60 行 + docs/08 立项块）
  → feat(api) 迁移 046 + models/items store 层 kind 放行 + recall category + import 端点
  → test(api) U1259–U1266
  → feat(frontend) 记忆页双标签 + 知识导入入口 + apiClient
  → test(frontend) U1267
  → docs 收口（docs/13 打包 AA 小节 + docs/107 §五 A1 标注闭合 + handoff）
```

- 零新依赖（embedding 复用 LocalDeterministicEmbedder）／无新 ADR／不解除 D35（记忆产品化余部独立）

## 五、风险与残余

- 知识/记忆混表：跨运营体共享触发时需拆表（预留 meta 无拆表钩子，届时立迁移）
- 本地词法向量召回精度有限：商业 embedding 触发（D35）时统一换 provider，本批零迁移
- 导入无幂等键：重复导入产生重复条目（MVP 接受；删除走既有 DELETE）
