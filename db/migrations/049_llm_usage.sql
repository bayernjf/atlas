-- 打包 AU（docs/129）：D70 LLM 用量与成本计量——llm_usage 表（token 用量持久化）。
-- 六个 litellm.completion 调用点此前丢弃回包 usage；本表是成本账的权威档：
-- 跨重启/跨连接存活，且里程碑 C（D69 Loop 预算闸门）要读历史窗口。
-- 内存档为进程内 ring 2000（llm/metering.py），reset 同清；本表 reset 同清
-- （运行计量数据，与 model_config 的"配置跨重置保留"区分）。
-- 索引 (tenant_id, ts) 供时间窗聚合；行内 tenant_id 过滤（非 RLS，照
-- reflection_* 先例）。不挂 ATLAS_RETENTION_DAYS（docs/129 AU-D7 余部）。

CREATE TABLE IF NOT EXISTS llm_usage (
    tenant_id         TEXT NOT NULL,
    ts                TEXT NOT NULL,
    caller            TEXT NOT NULL,
    node_id           TEXT NOT NULL DEFAULT '',
    graph_id          TEXT NOT NULL DEFAULT '',
    run_id            TEXT NOT NULL DEFAULT '',
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    latency_ms        DOUBLE PRECISION NOT NULL DEFAULT 0,
    status            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_tenant_ts ON llm_usage (tenant_id, ts);
