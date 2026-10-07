-- 打包 A1：模板库产品化收尾——版本/CAS＋使用统计＋参数化声明（docs/97）。
-- user_templates 加 version（乐观锁，PUT 递增）、updated_at（变更时间）、usage_count（显式 touch 计数）、
-- params（参数化向导声明，JSON Schema 子集）；存量行回填 version=1、updated_at=created_at、usage_count=0、params={}。
ALTER TABLE user_templates ADD COLUMN IF NOT EXISTS version BIGINT NOT NULL DEFAULT 1;
ALTER TABLE user_templates ADD COLUMN IF NOT EXISTS updated_at TEXT NOT NULL DEFAULT '';
ALTER TABLE user_templates ADD COLUMN IF NOT EXISTS usage_count BIGINT NOT NULL DEFAULT 0;
ALTER TABLE user_templates ADD COLUMN IF NOT EXISTS params JSONB NOT NULL DEFAULT '{}'::jsonb;
UPDATE user_templates SET updated_at = created_at WHERE updated_at = '';
COMMENT ON COLUMN user_templates.version IS '模板版本号（乐观锁 CAS；PUT 成功递增 1，缺省 1）';
COMMENT ON COLUMN user_templates.updated_at IS '最近变更 UTC ISO（add=created_at；PUT 刷新）';
COMMENT ON COLUMN user_templates.usage_count IS '从模板新建实例化计数（POST /usage 显式 +1，v1 不自动埋点）';
COMMENT ON COLUMN user_templates.params IS '参数化向导声明（JSON Schema 子集：type/label/required/default/hint/options，缺省 {}）';
