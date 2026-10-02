-- 打包 ZM：模板库产品化切片——分类管理（docs/08 打包 ZM 立项块）。
-- user_templates 加 category 列；内置模板（template/catalog.py 常量）不进库，无迁移面。
-- 缺省 ''＝未分类（列表投影与创建请求体同口径），存量行行为逐字不变。
ALTER TABLE user_templates ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT '';
COMMENT ON COLUMN user_templates.category IS '模板分类（≤30 字符，缺省空串＝未分类；内置目录按主题归类，用户模板自由填写）';
