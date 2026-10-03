-- 打包 Y（docs/93）：LLM 模型配置管理面——model_config 表（内置全局一份＋BYOK per-tenant 一份）。
-- 设计（契约 docs/93 §2.1）：内置模型用保留行 tenant_id='__builtin__'（平台级单一配置，admin 维护）；
-- BYOK 用真实 tenant_id 一行（租户管理员/operator 维护自己的 key）。单行 JSON 先例＝monitoring_rules（004）。
-- 密钥形态：api_key_enc 为 AES-GCM 信封（docs/32 T26，enc$v1$...），落库零明文；读回经
-- connections.service.get_secret_provider() 解密后只在调用点进程内存里显式传 litellm.completion(api_key=...)，
-- 不靠 env（docs/93 §7 风险点，验收 U1092 钉死）。
-- 幂等：CREATE TABLE IF NOT EXISTS 可重复；新装库由迁移运行器顺序应用 001→最新，空表零成本。
CREATE TABLE IF NOT EXISTS model_config (
    tenant_id TEXT PRIMARY KEY,
    config TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

COMMENT ON TABLE model_config IS
    'LLM 模型配置（docs/93 打包 Y）：tenant_id=__builtin__ 为内置模型（平台级一份），其余为各租户 BYOK；config 为 ModelConfig JSON（api_key_enc 为 AES-GCM 信封）';
