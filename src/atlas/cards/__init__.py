"""交互卡片库（M8：内置只读目录 + web/im/email 三渠道渲染，04 §5.6 / 12 §3.11）。"""

from atlas.cards.catalog import (
    CARDS,
    CardAction,
    CardFallback,
    CardTemplate,
    FieldBinding,
    FieldsSection,
    FormSection,
    get_card,
    list_cards,
)
from atlas.cards.render import (
    CardRenderError,
    map_action_output,
    render_card,
    to_message_params,
)

__all__ = [
    "CARDS",
    "CardAction",
    "CardFallback",
    "CardRenderError",
    "CardTemplate",
    "FieldBinding",
    "FieldsSection",
    "FormSection",
    "get_card",
    "list_cards",
    "map_action_output",
    "render_card",
    "to_message_params",
]
