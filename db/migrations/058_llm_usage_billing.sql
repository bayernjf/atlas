-- W5-4：LLM 用量记录补归属与计费来源——llm_usage 加 assignment_id / billing_source。
-- assignment_id 关联 hub 分配（asn_...），用于把内置模型用量回报 hub；
-- billing_source 区分 builtin（平台/hub/env 下发的 key）与 byok（租户自带 key）。
-- 幂等：ALTER TABLE ADD COLUMN IF NOT EXISTS 可重复执行。
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS assignment_id TEXT NOT NULL DEFAULT '';
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS billing_source TEXT NOT NULL DEFAULT '';
