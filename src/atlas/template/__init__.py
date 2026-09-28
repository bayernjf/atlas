"""流程模板库（内置只读目录 04 §5.10；用户自建模板 docs/85 打包 X）。"""

from atlas.template.catalog import TEMPLATES, TemplateMeta, get_template, list_templates
from atlas.template.user_store import UserTemplate, UserTemplateStore

__all__ = [
    "TEMPLATES",
    "TemplateMeta",
    "get_template",
    "list_templates",
    "UserTemplate",
    "UserTemplateStore",
]
