"""condition 节点首版规则表达式（04 §5.1 语法白名单 / §5.2 契约）。

手写 tokenize + 递归下降解析，禁止 eval，零第三方依赖。
语法：
    or_expr   := and_expr ('||' and_expr)*
    and_expr  := not_expr ('&&' not_expr)*
    not_expr  := '!' not_expr | comparison
    comparison := primary (op primary)?   op ∈ > >= < <= == !=
    primary   := '(' or_expr ')' | '{{路径}}' | 字面量
字面量：数字（含负号）、单/双引号字符串、true/false/null。
首版不支持算术、函数调用、裸标识符；LLM 判断分支缓做（14 登记表）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_TOKEN_RE = re.compile(
    r"\{\{\s*[^{}]+?\s*\}\}"  # {{路径}}
    r"|>=|<=|==|!=|&&|\|\||[()!><]"
    r"|-?\d+(?:\.\d+)?"
    r"|'(?:\\.|[^'\\])*'"
    r'|"(?:\\.|[^"\\])*"'
    r"|[A-Za-z_][A-Za-z0-9_]*"
)


class ConditionEvalError(Exception):
    """表达式求值期错误（含变量的类型错误、缺失变量参与有序比较等）。"""


@dataclass
class _Token:
    kind: str
    value: str
    pos: int


def _tokenize(expression: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    while pos < len(expression):
        if expression[pos].isspace():
            pos += 1
            continue
        match = _TOKEN_RE.match(expression, pos)
        if match is None or match.start() != pos:
            raise ConditionEvalError(f'语法错误：意外字符 "{expression[pos]}"（位置 {pos + 1}）')
        text = match.group(0)
        if text.startswith("{{"):
            kind = "path"
            value = text[2:-2].strip()
        elif text[0].isdigit() or (text.startswith("-") and len(text) > 1):
            kind, value = "number", text
        elif text[0] in ("'", '"'):
            kind, value = "string", text[1:-1]
        elif text in ("true", "false", "null"):
            kind, value = "literal", text
        elif text in ("&&", "||", "!", ">", ">=", "<", "<=", "==", "!=", "(", ")"):
            kind, value = "op", text
        else:
            raise ConditionEvalError(
                f'语法错误：未知标识符 "{text}"（位置 {pos + 1}；'
                "变量须用 {{路径}} 包裹，字面量仅支持 true/false/null、数字、字符串）"
            )
        tokens.append(_Token(kind, value, pos))
        pos = match.end()
    return tokens


class _Parser:
    def __init__(self, tokens: list[_Token]):
        self.tokens = tokens
        self.index = 0

    def _peek(self) -> _Token | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _consume(self) -> _Token:
        token = self._peek()
        if token is None:
            raise ConditionEvalError("语法错误：表达式不完整")
        self.index += 1
        return token

    def parse(self) -> tuple:
        ast = self._parse_or()
        if self._peek() is not None:
            token = self._peek()
            raise ConditionEvalError(f'语法错误：意外的 token "{token.value}"（位置 {token.pos + 1}）')
        return ast

    def _parse_or(self) -> tuple:
        node = self._parse_and()
        while (token := self._peek()) is not None and token.value == "||":
            self._consume()
            node = ("binary", "||", node, self._parse_and())
        return node

    def _parse_and(self) -> tuple:
        node = self._parse_not()
        while (token := self._peek()) is not None and token.value == "&&":
            self._consume()
            node = ("binary", "&&", node, self._parse_not())
        return node

    def _parse_not(self) -> tuple:
        token = self._peek()
        if token is not None and token.value == "!":
            self._consume()
            return ("unary", "not", self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> tuple:
        left = self._parse_primary()
        token = self._peek()
        if token is not None and token.value in (">", ">=", "<", "<=", "==", "!="):
            self._consume()
            right = self._parse_primary()
            return ("binary", token.value, left, right)
        return left

    def _parse_primary(self) -> tuple:
        token = self._consume()
        if token.value == "(":
            node = self._parse_or()
            closing = self._peek()
            if closing is None or closing.value != ")":
                raise ConditionEvalError(f"语法错误：缺少右括号（自位置 {token.pos + 1}）")
            self._consume()
            return node
        if token.kind == "path":
            return ("var", token.value)
        if token.kind == "number":
            return ("lit", float(token.value) if "." in token.value else int(token.value))
        if token.kind == "string":
            return ("lit", token.value)
        if token.kind == "literal":
            return ("lit", {"true": True, "false": False, "null": None}[token.value])
        raise ConditionEvalError(f'语法错误：意外的 token "{token.value}"（位置 {token.pos + 1}）')


def parse(expression: str) -> tuple:
    return _Parser(_tokenize(expression)).parse()


def validate_expression(expression: str) -> list[str]:
    """校验期检查：语法 + 纯字面量比较的静态类型；错误为中文列表。"""
    if not expression or not expression.strip():
        return ["表达式不能为空"]
    try:
        ast = parse(expression)
    except ConditionEvalError as exc:
        return [str(exc)]
    return _static_type_errors(ast)


def _has_var(node: tuple) -> bool:
    kind = node[0]
    if kind == "var":
        return True
    if kind == "lit":
        return False
    if kind == "unary":
        return _has_var(node[2])
    return _has_var(node[2]) or _has_var(node[3])


def _static_type_errors(node: tuple) -> list[str]:
    kind = node[0]
    if kind == "lit" or kind == "var":
        return []
    if kind == "unary":
        return _static_type_errors(node[2])
    errors = _static_type_errors(node[2]) + _static_type_errors(node[3])
    op = node[1]
    if op in (">", ">=", "<", "<=") and not _has_var(node[2]) and not _has_var(node[3]):
        try:
            _compare(op, _literal_value(node[2]), _literal_value(node[3]))
        except ConditionEvalError as exc:
            errors.append(str(exc))
    return errors


def _literal_value(node: tuple) -> Any:
    # 仅用于无变量子树；非 lit 的无变量表达式（true/false/括号布尔）交运行时规则处理。
    return node[1] if node[0] == "lit" else True


def evaluate_expression(expression: str, context: dict[str, Any]) -> bool:
    ast = parse(expression)
    return _evaluate(ast, context)


def _evaluate(node: tuple, context: dict[str, Any]) -> Any:
    kind = node[0]
    if kind == "lit":
        return node[1]
    if kind == "var":
        from atlas.graph.loader import resolve_path

        return resolve_path(node[1], context)
    if kind == "unary":
        value = _evaluate(node[2], context)
        if not isinstance(value, bool):
            raise ConditionEvalError(f'逻辑非 "!" 要求布尔值，实际为 {_type_name(value)}')
        return not value
    op, left_node, right_node = node[1], node[2], node[3]
    if op == "&&":
        left = _evaluate(left_node, context)
        if not isinstance(left, bool):
            raise ConditionEvalError(f'"&&" 要求布尔值，实际为 {_type_name(left)}')
        return left and _evaluate(right_node, context)
    if op == "||":
        left = _evaluate(left_node, context)
        if not isinstance(left, bool):
            raise ConditionEvalError(f'"||" 要求布尔值，实际为 {_type_name(left)}')
        return left or _evaluate(right_node, context)
    return _compare(op, _evaluate(left_node, context), _evaluate(right_node, context))


def _compare(op: str, left: Any, right: Any) -> bool:
    if op in ("==", "!="):
        equal = left == right
        return equal if op == "==" else not equal
    if left is None or right is None:
        raise ConditionEvalError("空值（null/缺失变量）只能做 == / != 比较，不能参与大小比较")
    if isinstance(left, bool) or isinstance(right, bool) or type(left) is not type(right):
        raise ConditionEvalError(
            f'有序比较 "{op}" 要求两侧同为数字或同为字符串，实际为 {_type_name(left)} 与 {_type_name(right)}'
        )
    if not isinstance(left, (int, float, str)):
        raise ConditionEvalError(f'有序比较 "{op}" 不支持类型 {_type_name(left)}')
    return {
        ">": left > right,
        ">=": left >= right,
        "<": left < right,
        "<=": left <= right,
    }[op]


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "布尔"
    if isinstance(value, (int, float)):
        return "数字"
    if isinstance(value, str):
        return "字符串"
    return type(value).__name__
