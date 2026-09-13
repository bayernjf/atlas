# Changelog

本文件记录 Atlas 仓库的可追溯变更里程碑。详细过程与状态见 [handoff.md](handoff.md)。

## [Unreleased]

### docs（2026-09-13）

- 技术选型 T1-T5 收口为正式 ADR（10 文档 §3/§4）：Demo 模型经 LiteLLM 对接主流商业 API（`.env` 切换，不引入 vLLM）；评估与优化层仅留抽象接口；编辑后台前端进 Demo 骨架（`frontend/`，W1-W2）；进程内事件总线替代 NATS（Phase 2 重启）；Harness 网关 Demo 用 Python/FastAPI 实现同构接口（Go 化留 Phase 2）。02/08/09 同步，14 文档 D5/D6 已登记缓做触发条件。

### docs（2026-09-13）

- 移除 `LICENSE`（用户指示：现在不需要，许可证暂未定）；README / CONTRIBUTING 的 License 段落与 handoff / CHANGELOG 引用同步清理。
- 补齐 agent-world 惯例规范文档：根目录 `CONTRIBUTING.md` / `MIGRATION_CONVENTION.md` / `BENCHMARK.md` / `.gitleaks.toml` / `.env.example`；docs 新增 `14-缓做事项登记表` / `15-环境与分支策略` / `16-反馈工作流`。索引同步 00 文档地图、README、handoff。

### chore / infra（2026-09-13）

- 仓库初始化：`git init -b main`，添加 Python 项目 `.gitignore`；创建 GitHub **private** 仓库 `bayernjf/atlas` 并推送（3 个原子提交：chore / docs specs / docs meta）。

### docs（2026-09-13）

- 建立根目录元文档体系（参考 agent-world 惯例）：`handoff.md` / `README.md` / `AGENTS.md` / `CLAUDE.md` / `git-commit-message.md` / `CHANGELOG.md`。
- 源文档切分完成并核对：两份原始方案文档按主题切分为 `docs/` 00-13 共 14 份 AI 导向文档，274 章节全映射、逐段覆盖率机器核对通过，源文档已删除（详见 [00-文档索引与治理.md](docs/00-文档索引与治理.md)）。
