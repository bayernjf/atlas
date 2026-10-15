-- 打包 BR（docs/134）：D77 A/B 实验对照判定——experiments 表（实验是配置资产，跨重启不可丢，
--   同 BQ adapter_drafts 理由）。
--   实验＝叠加在灰度面上的意图/判定层（BR-D1 分流复用 RoutingStore canary）：创建即启动
--   （写配置＋start 显式两版＋落库一步）→ GET 活判定 → conclude 人决（冻结 verdict＋
--   promote/rollback＋恢复 prior_config）。
-- 键形态：id TEXT 单列 PK（`exp-N`，数字部分取全局 storage_id_seq，跨实例不复用；
--   adapter_drafts/autoapply_runs 同款取号），tenant_id 行内过滤。同图同时刻至多一个
--   running 实验由代码侧保证（BR-D6：创建前 running_for_graph 检查＋409），不建部分唯一索引
--   （内存档无法同构约束，两档语义以代码为准）。
-- 时间列用 TEXT ISO-8601 而非 TIMESTAMPTZ：created_at/concluded_at 不参与认领/唯一判定，
--   读回投影与内存档逐键同型（字符串），无 datetime 转换漂移（031 rollout_states 同款口径）。
-- prior_config/verdict 用 JSONB：RolloutConfig/Verdict 的 model_dump 整体读改写。
-- 不做 retention：配置资产不进 prune_expired，不按保留期自动删。
-- 幂等：CREATE TABLE IF NOT EXISTS，可重复执行；新装库由迁移 CLI 顺序应用 001→最新。
CREATE TABLE IF NOT EXISTS experiments (
    id                TEXT PRIMARY KEY,   -- exp-N（storage_id_seq）
    tenant_id         TEXT NOT NULL,
    graph_id          TEXT NOT NULL,
    version_a         INTEGER NOT NULL,   -- 对照臂（创建时 stable）
    version_b         INTEGER NOT NULL,   -- 实验臂（创建时 candidate，canary 流量段）
    traffic_percent   INTEGER NOT NULL,   -- canary 百分比＝B 臂流量（1-99）
    primary_metric    TEXT NOT NULL,      -- GateMetricId 三选一（低率为优）
    min_samples       INTEGER NOT NULL,   -- 每臂主指标样本门（固定地平线纪律）
    status            TEXT NOT NULL,      -- running|concluded
    prior_config      JSONB,              -- 启动前 RolloutConfig 快照（conclude 恢复用，BR-D5）
    verdict           JSONB,              -- conclude 时刻冻结的 Verdict（model_dump）
    conclusion_action TEXT,               -- adopt_a|adopt_b（人决，可与 verdict 不一致，BR-D4）
    conclusion_reason TEXT,
    created_by        TEXT NOT NULL,
    created_at        TEXT NOT NULL,      -- ISO-8601
    concluded_at      TEXT
);
COMMENT ON TABLE experiments IS 'A/B 实验对照判定（打包 BR/docs/134＝D77；里程碑 G）';
COMMENT ON COLUMN experiments.id IS 'exp-N；数字部分取全局 storage_id_seq，跨实例不复用';
COMMENT ON COLUMN experiments.primary_metric IS 'run_error_rate|manual_escalation_rate|refund_amount_diff_rate，与 gate 同 id 同分母（BR-D3）';
COMMENT ON COLUMN experiments.prior_config IS '启动前 RolloutConfig model_dump；conclude 恢复（无则留实验配置）';
COMMENT ON COLUMN experiments.verdict IS 'conclude 冻结的显著性判定；GET detail 的活算 verdict 不落库';
COMMENT ON COLUMN experiments.status IS 'running|concluded；同图至多一个 running 由代码侧保证（BR-D6）';

CREATE INDEX IF NOT EXISTS idx_experiments_tenant_graph_status
    ON experiments (tenant_id, graph_id, status);
