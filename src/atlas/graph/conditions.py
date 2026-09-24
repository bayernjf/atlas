"""condition/loop 安全规则表达式（04 §5.1 语法白名单 / §5.2 契约；D15 扩算术与函数）。

手写 tokenize + 递归下降解析，禁止 eval，零第三方依赖。
语法（D15 在比较之下加入算术层与白名单函数）：
    or_expr      := and_expr ('||' and_expr)*
    and_expr     := not_expr ('&&' not_expr)*
    not_expr     := '!' not_expr | comparison
    comparison   := additive (cmp_op additive)?    cmp_op ∈ > >= < <= == !=
    additive     := multiplicative (('+'|'-') multiplicative)*
    multiplicative := unary (('*'|'/'|'%') unary)*
    unary        := ('!'|'-'|'+') unary | primary
    primary      := '(' or_expr ')' | '{{路径}}' | 字面量 | 函数调用
    函数调用     := NAME '(' [or_expr (',' or_expr)*] ')'   仅白名单函数
字面量：数字、单/双引号字符串、true/false/null；日期由 date(y,m,d) 构造、
日期时间由 datetime(y,m,d,H,M[,S]) 构造（统一 UTC）。
顶层表达式必须产出布尔值（比较/逻辑运算）；纯算术/常量非布尔在 validate 期报错。
白名单函数（无副作用；D15 算术/函数已落，today/now 为非确定函数，其值由可注入时钟决定）：
    数值 abs/floor/ceil/round/min/max；字符串 len/lower/upper；
    日期 date/year/month/day/daysBetween/today；
    日期时间 now/datetime/hoursBetween（today()/now() 取注入时钟，录制/回放冻结以保确定性）。
today()/now() 为非确定函数：evaluate_expression(..., now=) 可注入时钟（单次运行固定、
回放冻结到用例 recorded_at）；不传则取当前 UTC。date 与 datetime 不可跨类型直接比较。
LLM 判断分支（D14）、foreach（D16）、随机/UUID/命名时区仍缓做（见 14 登记表）。
"""

from __future__ import annotations

import datetime
import math
import re
from dataclasses import dataclass
from typing import Any

_TOKEN_RE = re.compile(
    r"\{\{\s*[^{}]+?\s*\}\}"  # {{路径}}
    r"|>=|<=|==|!=|&&|\|\||[()!><+\-*/%,]"  # 运算符与逗号（负号由 parser 一元处理）
    r"|\d+(?:\.\d+)?"
    r"|'(?:\\.|[^'\\])*'"
    r'|"(?:\\.|[^"\\])*"'
    r"|[A-Za-z_][A-Za-z0-9_]*"
)

_CMP_OPS = (">", ">=", "<", "<=", "==", "!=")
_LOGIC_OPS = ("&&", "||")
_ADD_OPS = ("+", "-")
_MUL_OPS = ("*", "/", "%")
_UNARY_OPS = ("!", "-", "+")


class ConditionEvalError(Exception):
    """表达式求值期错误（含变量的类型错误、缺失变量参与有序比较等）。

    docs/60 G1：除中文 message 外携带机器可读 code 与 params（英文类型码，便于前端
    英文态模板插值）；中文 message 始终是兜底真相，str(exc) 形状不变。
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "COND_EVAL_FAILED",
        params: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.params = params or {}


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
            raise ConditionEvalError(f'语法错误：意外字符 "{expression[pos]}"（位置 {pos + 1}）', code="COND_SYNTAX_UNEXPECTED_CHAR", params={"token": expression[pos], "pos": pos + 1})
        text = match.group(0)
        if text.startswith("{{"):
            kind = "path"
            value = text[2:-2].strip()
        elif text[0].isdigit():
            kind, value = "number", text
        elif text[0] in ("'", '"'):
            kind, value = "string", text[1:-1]
        elif text in ("true", "false", "null"):
            kind, value = "literal", text
        elif text in _CMP_OPS + _LOGIC_OPS + _ADD_OPS + _MUL_OPS + ("(", ")", "!", ","):
            kind, value = "op", text
        else:
            # 裸标识符：函数名在 parser 结合其后的 '(' 判定，其余为未知标识符。
            kind, value = "ident", text
        tokens.append(_Token(kind, value, pos))
        pos = match.end()
    return tokens


# 白名单函数：name -> (最少参数数, 最多参数数或 None 表示不限, 返回类型)。
# 返回类型：number/string/date/datetime/any。
_FUNCTIONS: dict[str, tuple[int, int | None, str]] = {
    "abs": (1, 1, "number"),
    "floor": (1, 1, "number"),
    "ceil": (1, 1, "number"),
    "round": (1, 1, "number"),
    "min": (1, None, "number"),
    "max": (1, None, "number"),
    "len": (1, 1, "number"),
    "lower": (1, 1, "string"),
    "upper": (1, 1, "string"),
    "date": (3, 3, "date"),
    "year": (1, 1, "number"),
    "month": (1, 1, "number"),
    "day": (1, 1, "number"),
    "daysBetween": (2, 2, "number"),
    # 非确定日期/时间（取注入时钟，单次运行固定、回放冻结）与 UTC datetime 体系（D15 余部，docs/27 C）。
    "today": (0, 0, "date"),
    "now": (0, 0, "datetime"),
    "datetime": (5, 6, "datetime"),
    "hoursBetween": (2, 2, "number"),
}

# 非确定函数：静态校验期不做常量折叠（其值依赖运行时钟），其余纯函数仍折叠暴露错误。
_NONDETERMINISTIC = frozenset({"today", "now"})


def _default_now() -> datetime.datetime:
    """缺省时钟：当前 UTC（aware）。"""
    return datetime.datetime.now(datetime.timezone.utc)


def _ensure_utc(value: datetime.datetime) -> datetime.datetime:
    """归一化为 aware UTC：naive datetime 视为 UTC，aware 转到 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc)


class _Parser:
    def __init__(self, tokens: list[_Token]):
        self.tokens = tokens
        self.index = 0

    def _peek(self) -> _Token | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _consume(self) -> _Token:
        token = self._peek()
        if token is None:
            raise ConditionEvalError("语法错误：表达式不完整", code="COND_SYNTAX_INCOMPLETE")
        self.index += 1
        return token

    def parse(self) -> tuple:
        ast = self._parse_or()
        if self._peek() is not None:
            token = self._peek()
            raise ConditionEvalError(f'语法错误：意外的 token "{token.value}"（位置 {token.pos + 1}）', code="COND_SYNTAX_UNEXPECTED_TOKEN", params={"token": token.value, "pos": token.pos + 1})
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
            return ("unary", "!", self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> tuple:
        left = self._parse_additive()
        token = self._peek()
        if token is not None and token.value in _CMP_OPS:
            self._consume()
            right = self._parse_additive()
            return ("binary", token.value, left, right)
        return left

    def _parse_additive(self) -> tuple:
        node = self._parse_multiplicative()
        while (token := self._peek()) is not None and token.value in _ADD_OPS:
            self._consume()
            node = ("binary", token.value, node, self._parse_multiplicative())
        return node

    def _parse_multiplicative(self) -> tuple:
        node = self._parse_unary()
        while (token := self._peek()) is not None and token.value in _MUL_OPS:
            self._consume()
            node = ("binary", token.value, node, self._parse_unary())
        return node

    def _parse_unary(self) -> tuple:
        token = self._peek()
        if token is not None and token.value in ("!", "-", "+"):
            self._consume()
            return ("unary", token.value, self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> tuple:
        token = self._consume()
        if token.value == "(":
            node = self._parse_or()
            closing = self._peek()
            if closing is None or closing.value != ")":
                raise ConditionEvalError(f"语法错误：缺少右括号（自位置 {token.pos + 1}）", code="COND_SYNTAX_MISSING_PAREN", params={"pos": token.pos + 1})
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
        if token.kind == "ident":
            return self._parse_identifier(token)
        raise ConditionEvalError(f'语法错误：意外的 token "{token.value}"（位置 {token.pos + 1}）', code="COND_SYNTAX_UNEXPECTED_TOKEN", params={"token": token.value, "pos": token.pos + 1})

    def _parse_identifier(self, token: _Token) -> tuple:
        nxt = self._peek()
        if nxt is None or nxt.value != "(":
            raise ConditionEvalError(
                f'语法错误：未知标识符 "{token.value}"（位置 {token.pos + 1}；'
                "变量须用 {{路径}} 包裹，字面量仅支持 true/false/null、数字、字符串，"
                f"函数仅限白名单：{', '.join(_FUNCTIONS)}）",
                code="COND_UNKNOWN_IDENTIFIER",
                params={"token": token.value, "pos": token.pos + 1},
            )
        # 函数调用
        self._consume()  # '('
        args: list[tuple] = []
        if self._peek() is not None and self._peek().value != ")":
            args.append(self._parse_or())
            while self._peek() is not None and self._peek().value == ",":
                self._consume()
                args.append(self._parse_or())
        closing = self._peek()
        if closing is None or closing.value != ")":
            raise ConditionEvalError(f'语法错误：函数 "{token.value}" 缺少右括号', code="COND_SYNTAX_MISSING_PAREN", params={"func": token.value})
        self._consume()  # ')'
        if token.value not in _FUNCTIONS:
            raise ConditionEvalError(
                f'语法错误：未知函数 "{token.value}"（仅支持白名单函数：{", ".join(_FUNCTIONS)}）',
                code="COND_UNKNOWN_FUNC",
                params={"func": token.value},
            )
        min_args, max_args, _return_type = _FUNCTIONS[token.value]
        if len(args) < min_args or (max_args is not None and len(args) > max_args):
            if min_args == max_args:
                want = f"{min_args} 个参数"
            else:
                want = f"至少 {min_args} 个参数" if max_args is None else f"{min_args}-{max_args} 个参数"
            raise ConditionEvalError(f'函数 "{token.value}" 需要{want}，实际 {len(args)} 个', code="COND_FUNC_ARITY", params={"func": token.value, "actual": len(args)})
        return ("call", token.value, tuple(args))


def parse(expression: str) -> tuple:
    return _Parser(_tokenize(expression)).parse()


def validate_expression(expression: str) -> list[str]:
    """校验期检查：语法 + 静态类型（常量折叠求值 + 顶层须为布尔）；错误为中文列表。"""
    if not expression or not expression.strip():
        return ["表达式不能为空"]
    try:
        ast = parse(expression)
    except ConditionEvalError as exc:
        return [str(exc)]
    errors = _static_type_errors(ast)
    # 顶层必须产出布尔：静态可判定为非布尔（数值/字符串/日期）即报错；类型未知（变量）放行。
    top_type = _infer_type(ast)
    if top_type in ("number", "string", "date", "datetime"):
        errors.append("条件表达式必须产出布尔值（比较或逻辑运算），不能直接使用算术结果/数值/字符串/日期")
    return errors


def _has_var(node: tuple) -> bool:
    kind = node[0]
    if kind == "var":
        return True
    if kind == "lit":
        return False
    if kind == "unary":
        return _has_var(node[2])
    if kind == "call":
        return any(_has_var(arg) for arg in node[2])
    return _has_var(node[2]) or _has_var(node[3])


def _infer_type(node: tuple) -> str:
    """静态推断产出类型：bool/number/string/date/null/unknown（var 与含 var 表达式为 unknown）。"""
    kind = node[0]
    if kind == "var":
        return "unknown"
    if kind == "lit":
        value = node[1]
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, datetime.datetime):
            return "datetime"
        if isinstance(value, datetime.date):
            return "date"
        return "null"
    if kind == "unary":
        return "bool" if node[1] == "!" else "number"
    if kind == "call":
        return _FUNCTIONS[node[1]][2]
    op = node[1]
    if op in _CMP_OPS or op in _LOGIC_OPS:
        return "bool"
    return "number"  # 算术


def _static_type_errors(node: tuple) -> list[str]:
    kind = node[0]
    if kind in ("lit", "var"):
        return []
    if kind == "unary":
        return _static_type_errors(node[2])
    if kind == "call":
        errors: list[str] = []
        for arg in node[2]:
            errors.extend(_static_type_errors(arg))
        # 纯常量调用：折叠求值以静态暴露除零/参数类型/非法日期等错误。
        # today()/now() 依赖运行时钟，非确定，静态期不折叠（其类型由 _FUNCTIONS 推断）。
        if node[1] not in _NONDETERMINISTIC and not any(_has_var(arg) for arg in node[2]):
            try:
                _evaluate(node, {}, None)
            except ConditionEvalError as exc:
                errors.append(str(exc))
        return errors
    errors = _static_type_errors(node[2]) + _static_type_errors(node[3])
    op = node[1]
    if op in _CMP_OPS:
        if not _has_var(node[2]) and not _has_var(node[3]):
            try:
                _compare(op, _evaluate(node[2], {}, None), _evaluate(node[3], {}, None))
            except ConditionEvalError as exc:
                errors.append(str(exc))
    elif op in _ADD_OPS + _MUL_OPS:
        # 纯常量算术折叠；含变量子树的类型错误留运行时 fail-safe。
        if not _has_var(node[2]) and not _has_var(node[3]):
            try:
                _evaluate(node, {}, None)
            except ConditionEvalError as exc:
                errors.append(str(exc))
    return errors


def evaluate_expression(
    expression: str, context: dict[str, Any], *, now: datetime.datetime | None = None
) -> bool:
    ast = parse(expression)
    return _evaluate(ast, context, _ensure_utc(now) if now is not None else _default_now())


def _evaluate(node: tuple, context: dict[str, Any], now: datetime.datetime | None) -> Any:
    kind = node[0]
    if kind == "lit":
        return node[1]
    if kind == "var":
        from atlas.graph.loader import resolve_path

        return resolve_path(node[1], context)
    if kind == "unary":
        op = node[1]
        value = _evaluate(node[2], context, now)
        if op == "!":
            if not isinstance(value, bool):
                raise ConditionEvalError(f'逻辑非 "!" 要求布尔值，实际为 {_type_name(value)}', code="COND_TYPE_MISMATCH", params={"op": "!", "expected": "boolean", "actual": _type_code(value)})
            return not value
        # 一元 +/- 要求数值
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConditionEvalError(f'一元 "{op}" 要求数值，实际为 {_type_name(value)}', code="COND_TYPE_MISMATCH", params={"op": op, "expected": "number", "actual": _type_code(value)})
        return +value if op == "+" else -value
    if kind == "call":
        return _evaluate_function(
            node[1], [_evaluate(arg, context, now) for arg in node[2]], now
        )
    op, left_node, right_node = node[1], node[2], node[3]
    if op == "&&":
        left = _evaluate(left_node, context, now)
        if not isinstance(left, bool):
            raise ConditionEvalError(f'"&&" 要求布尔值，实际为 {_type_name(left)}', code="COND_TYPE_MISMATCH", params={"op": "&&", "expected": "boolean", "actual": _type_code(left)})
        return left and _evaluate(right_node, context, now)
    if op == "||":
        left = _evaluate(left_node, context, now)
        if not isinstance(left, bool):
            raise ConditionEvalError(f'"||" 要求布尔值，实际为 {_type_name(left)}', code="COND_TYPE_MISMATCH", params={"op": "||", "expected": "boolean", "actual": _type_code(left)})
        return left or _evaluate(right_node, context, now)
    if op in _CMP_OPS:
        return _compare(
            op,
            _evaluate(left_node, context, now),
            _evaluate(right_node, context, now),
        )
    return _arith(
        op,
        _evaluate(left_node, context, now),
        _evaluate(right_node, context, now),
    )


def _arith(op: str, left: Any, right: Any) -> Any:
    if isinstance(left, bool) or isinstance(right, bool) or not (
        isinstance(left, (int, float)) and isinstance(right, (int, float))
    ):
        raise ConditionEvalError(
            f'算术 "{op}" 要求两侧均为数值，实际为 {_type_name(left)} 与 {_type_name(right)}',
            code="COND_TYPE_MISMATCH",
            params={"op": op, "expected": "number", "actual": _type_code(left)},
        )
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    if op == "/":
        if right == 0:
            raise ConditionEvalError('算术 "/" 除数不能为 0', code="COND_DIVIDE_BY_ZERO", params={"op": "/"})
        return left / right
    # 截断式余数（与前端 JS Math.trunc 语义对齐，负数余数符号随被除数）
    if right == 0:
        raise ConditionEvalError('算术 "%" 模数不能为 0', code="COND_DIVIDE_BY_ZERO", params={"op": "%"})
    return left - right * math.trunc(left / right)


def _evaluate_function(
    name: str, args: list[Any], now: datetime.datetime | None
) -> Any:
    if name == "abs":
        _require_number(name, args[0])
        return abs(args[0])
    if name == "floor":
        _require_number(name, args[0])
        return math.floor(args[0])
    if name == "ceil":
        _require_number(name, args[0])
        return math.ceil(args[0])
    if name == "round":
        _require_number(name, args[0])
        # 半值向正无穷（floor(x+0.5)），与前端同构
        return math.floor(args[0] + 0.5)
    if name in ("min", "max"):
        for value in args:
            _require_number(name, value)
        return min(args) if name == "min" else max(args)
    if name == "len":
        value = args[0]
        if isinstance(value, str):
            return len(value)
        if isinstance(value, (list, dict)):
            return len(value)
        raise ConditionEvalError(f'函数 "len" 要求字符串/数组/对象，实际为 {_type_name(value)}', code="COND_TYPE_MISMATCH", params={"func": "len", "expected": "string|array|object", "actual": _type_code(value)})
    if name in ("lower", "upper"):
        if not isinstance(args[0], str):
            raise ConditionEvalError(f'函数 "{name}" 要求字符串，实际为 {_type_name(args[0])}', code="COND_TYPE_MISMATCH", params={"func": name, "expected": "string", "actual": _type_code(args[0])})
        return args[0].lower() if name == "lower" else args[0].upper()
    if name == "date":
        year, month, day = args
        for component, component_zh, value in (
            ("year", "年", year), ("month", "月", month), ("day", "日", day),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConditionEvalError(f'函数 "date" 的{component_zh}份必须是整数', code="COND_TYPE_MISMATCH", params={"func": "date", "expected": "integer", "component": component})
        try:
            return datetime.date(year, month, day)
        except ValueError as exc:
            raise ConditionEvalError(f'函数 "date" 构造了非法日期：{exc}', code="COND_INVALID_DATE", params={"func": "date"}) from None
    if name in ("year", "month", "day"):
        if not isinstance(args[0], datetime.date):
            raise ConditionEvalError(f'函数 "{name}" 要求日期值（用 date(y,m,d) 构造），实际为 {_type_name(args[0])}', code="COND_TYPE_MISMATCH", params={"func": name, "expected": "date", "actual": _type_code(args[0])})
        return getattr(args[0], name)
    if name == "daysBetween":
        start, end = args
        # datetime 先取日期（docs/27 §2.3），再按整日差。
        start_d = start.date() if isinstance(start, datetime.datetime) else start
        end_d = end.date() if isinstance(end, datetime.datetime) else end
        if not isinstance(start_d, datetime.date) or not isinstance(end_d, datetime.date):
            raise ConditionEvalError(
                f'函数 "daysBetween" 要求两个日期值，实际为 {_type_name(start)} 与 {_type_name(end)}',
                code="COND_TYPE_MISMATCH",
                params={"func": "daysBetween", "expected": "date", "actual": _type_code(start)},
            )
        return (end_d - start_d).days
    if name == "today":
        clock = now if now is not None else _default_now()
        return _ensure_utc(clock).date()
    if name == "now":
        clock = now if now is not None else _default_now()
        return _ensure_utc(clock)
    if name == "datetime":
        if len(args) == 5:
            year, month, day, hour, minute = args
            second = 0
        else:
            year, month, day, hour, minute, second = args
        for component, component_zh, value in (
            ("year", "年", year), ("month", "月", month), ("day", "日", day),
            ("hour", "小时", hour), ("minute", "分钟", minute), ("second", "秒", second),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConditionEvalError(f'函数 "datetime" 的{component_zh}必须是整数', code="COND_TYPE_MISMATCH", params={"func": "datetime", "expected": "integer", "component": component})
        try:
            return datetime.datetime(
                year, month, day, hour, minute, second, tzinfo=datetime.timezone.utc
            )
        except ValueError as exc:
            raise ConditionEvalError(f'函数 "datetime" 构造了非法日期时间：{exc}', code="COND_INVALID_DATE", params={"func": "datetime"}) from None
    if name == "hoursBetween":
        start, end = args
        start_dt = _coerce_datetime(start)
        end_dt = _coerce_datetime(end)
        return (end_dt - start_dt).total_seconds() / 3600
    raise ConditionEvalError(f'未知函数 "{name}"', code="COND_UNKNOWN_FUNC", params={"func": name})  # 理论不可达（parse 已拦）


def _coerce_datetime(value: Any) -> datetime.datetime:
    """hoursBetween 入参归一化：datetime 转 UTC；date 按当日 00:00 UTC；其余报错。"""
    if isinstance(value, datetime.datetime):
        return _ensure_utc(value)
    if isinstance(value, datetime.date):
        return datetime.datetime(
            value.year, value.month, value.day, tzinfo=datetime.timezone.utc
        )
    raise ConditionEvalError(
        '函数 "hoursBetween" 要求日期时间值（用 datetime(...) 或 now() 构造，日期按当日 00:00 UTC），'
        f"实际为 {_type_name(value)}",
        code="COND_TYPE_MISMATCH",
        params={"func": "hoursBetween", "expected": "datetime", "actual": _type_code(value)},
    )


def _require_number(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConditionEvalError(f'函数 "{name}" 要求数值参数，实际为 {_type_name(value)}', code="COND_TYPE_MISMATCH", params={"func": name, "expected": "number", "actual": _type_code(value)})


def _compare(op: str, left: Any, right: Any) -> bool:
    if op in ("==", "!="):
        equal = left == right
        return equal if op == "==" else not equal
    if left is None or right is None:
        raise ConditionEvalError("空值（null/缺失变量）只能做 == / != 比较，不能参与大小比较", code="COND_NULL_COMPARISON")
    if isinstance(left, bool) or isinstance(right, bool) or type(left) is not type(right):
        raise ConditionEvalError(
            f'有序比较 "{op}" 要求两侧同为数字、字符串或日期，实际为 {_type_name(left)} 与 {_type_name(right)}',
            code="COND_TYPE_MISMATCH",
            params={"op": op, "expected": "number|string|date", "actual": _type_code(left)},
        )
    if not isinstance(left, (int, float, str, datetime.date)):
        raise ConditionEvalError(f'有序比较 "{op}" 不支持类型 {_type_name(left)}', code="COND_TYPE_MISMATCH", params={"op": op, "actual": _type_code(left)})
    return {
        ">": left > right,
        ">=": left >= right,
        "<": left < right,
        "<=": left <= right,
    }[op]


def _type_code(value: Any) -> str:
    """与 _type_name 对应的英文机器码（docs/60 G1 params，供前端英文模板插值）。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, datetime.datetime):
        return "datetime"
    if isinstance(value, datetime.date):
        return "date"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "布尔"
    if isinstance(value, (int, float)):
        return "数字"
    if isinstance(value, str):
        return "字符串"
    if isinstance(value, datetime.datetime):
        return "日期时间"
    if isinstance(value, datetime.date):
        return "日期"
    return type(value).__name__
