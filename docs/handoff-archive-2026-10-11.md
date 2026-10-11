# handoff 归档（2026-10-11）

> 自 handoff「Recently shipped」5 条上限滚下的条目，原文逐字保留，编号／批次名不改。

## 滚出条目

✅ **打包 AJ 落码收口＝节点级 on_error 真执行（docs/121；2026-10-10）**：`retry.on_error` stop/continue/jump_to 与 max_retries 从纸面声明转真执行——stop 穿透令 run failed、continue 显式放行、jump_to 经 error_target 路由；backoff 编译期校验；前端 errorTarget 面板＋序列化 camelCase↔snake_case 打通。门：后端 2596/173/0、前端 972/2、oxlint/tsc/build 过。零新依赖/零迁移/无 ADR；全局异常/自愈/状态机子图见 D63。
