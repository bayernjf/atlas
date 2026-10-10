# handoff「Recently shipped」滚动存档（2026-10-10）

> 从 [handoff.md](../handoff.md)「Recently shipped」滚出的条目，逐字保留（该段只留最近 5 条）。完整过程与门读数见 [CHANGELOG.md](../CHANGELOG.md) 与 [docs/13](13-测试用例清单.md)。

## 滚出条目

✅ **RolloutModal 规则区迁 forms 落码收口＝D29 解锁路径第 3 步第二片（docs/120；2026-10-10，同日契约立项/落码/收口）**：规则区三行手写迁 forms 判别异构数组——`buildRolloutRulesSchema()`（items.oneOf 三分支、判别键 to、编辑面不含 full）＋`normalizeRolloutRules()`（段唯一＋固定序、bucket 注入 field/op）＋FormRenderer `disabled` prop（fieldset）＋判别键切换交互（ArrayView 判别数组行头段切换 Select、`oneOfBranchDefault` 目标分支默认对象、行内 const 判别键隐藏）；RolloutModal 删 withRule/findRule 手写三行改挂 FormRenderer；i18n 三字段 label 键。UI 行为变化照实（三行开关→数组行增删＋段切换、tenants 逗号→嵌套数组、rulesLocked 行内灰化→整区 fieldset、段 label 显示 const 原样）。门：前端 vitest **972/2**（+16）、oxlint/tsc/build 过、守护门 8 passed；真实浏览器冒烟段切换全链路通过（行值替换＋段唯一收敛）。零新依赖/零迁移/无 ADR。D29 第 3 步闭环，六类实体通用化等整体仍缓做。
