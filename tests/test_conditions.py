import pytest

from atlas.graph.conditions import ConditionEvalError, evaluate_expression, validate_expression


def test_comparison_and_precedence():
    assert evaluate_expression("1 < 2", {}) is True
    assert evaluate_expression("1 < 2 || 2 < 1 && 1 > 2", {}) is True
    assert evaluate_expression("(1 > 2 || 2 > 1) && 3 >= 3", {}) is True
    assert evaluate_expression("1 != 2 && 2 <= 2", {}) is True


def test_short_circuit_does_not_evaluate_unmatched_branch():
    assert evaluate_expression("false && {{missing}} > 1", {}) is False
    assert evaluate_expression("true || {{missing}} > 1", {}) is True


def test_not_and_booleans():
    assert evaluate_expression("!(1 > 2) && true || false", {}) is True


def test_string_literals_and_paths():
    assert evaluate_expression("{{status}} == 'paid'", {"status": "paid"}) is True
    assert evaluate_expression('{{status}} != "paid"', {"status": "pending"}) is True
    assert evaluate_expression("{{global.limit}} >= 500", {"global": {"limit": 500}}) is True


def test_null_comparison():
    assert evaluate_expression("{{missing}} == null", {}) is True
    assert evaluate_expression("{{x}} != null", {"x": 1}) is True
    with pytest.raises(ConditionEvalError):
        evaluate_expression("{{missing}} > 1", {})


def test_runtime_type_errors_fail_safe_signal():
    with pytest.raises(ConditionEvalError):
        evaluate_expression("{{name}} > 1", {"name": "alice"})
    with pytest.raises(ConditionEvalError):
        evaluate_expression("{{flag}} && true", {"flag": "yes"})
    with pytest.raises(ConditionEvalError):
        evaluate_expression("!{{flag}}", {"flag": 1})


def test_validate_syntax_errors():
    errors = validate_expression("amount >")
    assert errors and "语法错误" in errors[0]
    errors = validate_expression("foo == 1")
    assert errors and "未知标识符" in errors[0]
    errors = validate_expression("(1 > 2")
    assert errors and "括号" in errors[0]
    assert validate_expression("1 + 1")  # 算术合法但顶层须为布尔：报静态类型错误
    errors = validate_expression("1 + 1")
    assert errors and "布尔值" in errors[0]
    assert validate_expression("   ") == ["表达式不能为空"]


def test_validate_literal_type_error_is_static():
    errors = validate_expression("'a' > 1")
    assert errors and "同为数字" in errors[0]
    # 含变量的类型错误无法静态发现，校验期放行（运行时 fail-safe）
    assert validate_expression("{{name}} > 1") == []


def test_valid_expressions_pass_validation():
    assert validate_expression("{{trigger-1.context.payload.amount}} > 1000 && {{status}} == 'paid'") == []
    assert validate_expression("!({{x}} == null) || {{y}} >= -10") == []


# ---------- D15：算术、白名单函数、日期 ----------

def test_arith_precedence_and_parens():
    assert evaluate_expression("1 + 2 * 3 == 7", {}) is True
    assert evaluate_expression("(1 + 2) * 3 == 9", {}) is True
    assert evaluate_expression("10 - 2 - 3 == 5", {}) is True  # 左结合
    assert evaluate_expression("10 / 4 == 2.5", {}) is True
    assert evaluate_expression("7 % 3 == 1", {}) is True
    # 截断式余数与 JS 同构（负数余数符号随被除数）
    assert evaluate_expression("-7 % 3 == -1", {}) is True
    assert evaluate_expression("-2 * -3 == 6", {}) is True
    assert evaluate_expression("{{x}} * 2 > 10", {"x": 6}) is True


def test_unary_minus_number_literal():
    assert evaluate_expression("{{y}} >= -10", {"y": -5}) is True
    assert validate_expression("-5 + 1")  # 顶层算术非布尔 → 报错
    assert validate_expression("-5 < 1") == []


def test_arith_runtime_type_and_divzero_fail_safe():
    with pytest.raises(ConditionEvalError):
        evaluate_expression("{{s}} + 1", {"s": "x"})
    with pytest.raises(ConditionEvalError):
        evaluate_expression("1 / 0 == 0", {})
    with pytest.raises(ConditionEvalError):
        evaluate_expression("1 % 0 == 0", {})
    # 常量折叠：除零/类型错在 validate 期即可静态发现
    assert validate_expression("1 / 0 == 0")
    assert validate_expression("'a' + 1 == 'a1'")
    # 含变量的算术类型错校验期放行，运行时 fail-safe
    assert validate_expression("{{s}} + 1 == 2") == []


def test_numeric_functions():
    assert evaluate_expression("abs(-3) == 3", {}) is True
    assert evaluate_expression("floor(2.8) == 2", {}) is True
    assert evaluate_expression("ceil(2.1) == 3", {}) is True
    assert evaluate_expression("round(2.5) == 3", {}) is True
    assert evaluate_expression("round(2.4) == 2", {}) is True
    assert evaluate_expression("round(-1.5) == -1", {}) is True  # floor(x+0.5)
    assert evaluate_expression("min(3, 1, 2) == 1", {}) is True
    assert evaluate_expression("max(3, 1, 2) == 3", {}) is True
    assert evaluate_expression("max({{a}}, {{b}}) == 9", {"a": 9, "b": 4}) is True


def test_string_functions():
    assert evaluate_expression("len('hello') == 5", {}) is True
    assert evaluate_expression("len({{arr}}) == 3", {"arr": [1, 2, 3]}) is True
    assert evaluate_expression("lower('AbC') == 'abc'", {}) is True
    assert evaluate_expression("upper('AbC') == 'ABC'", {}) is True
    assert evaluate_expression("upper({{name}}) == 'ALICE'", {"name": "alice"}) is True
    with pytest.raises(ConditionEvalError):
        evaluate_expression("len(1) == 1", {})


def test_date_functions_and_comparison():
    assert evaluate_expression("date(2026, 1, 1) < date(2026, 2, 1)", {}) is True
    assert evaluate_expression("year(date(2026, 9, 19)) == 2026", {}) is True
    assert evaluate_expression("month(date(2026, 9, 19)) == 9", {}) is True
    assert evaluate_expression("day(date(2026, 9, 19)) == 19", {}) is True
    assert evaluate_expression("daysBetween(date(2026,1,1), date(2026,1,11)) == 10", {}) is True
    assert evaluate_expression("daysBetween(date(2026,1,11), date(2026,1,1)) == -10", {}) is True
    # 非法日期 / 错误参数类型 fail-safe
    with pytest.raises(ConditionEvalError):
        evaluate_expression("date(2026, 13, 1) == date(2026, 13, 1)", {})
    with pytest.raises(ConditionEvalError):
        evaluate_expression("year({{x}}) == 2026", {"x": "notadate"})
    assert validate_expression("date(2026, 13, 1) < date(2027,1,1)")  # 常量折叠静态报错
    # 日期不能与数字有序比较
    with pytest.raises(ConditionEvalError):
        evaluate_expression("date(2026,1,1) > 1", {})


def test_unknown_function_and_arity():
    errors = validate_expression("foo(1) == 1")
    assert errors and "未知函数" in errors[0]
    errors = validate_expression("abs() == 0")
    assert errors and "参数" in errors[0]
    errors = validate_expression("date(2026, 1) == date(2026,1)")
    assert errors and "3 个参数" in errors[0]
    # 裸标识符（非函数调用）仍是未知标识符
    errors = validate_expression("bar == 1")
    assert errors and "未知标识符" in errors[0]


def test_top_level_must_be_boolean():
    assert validate_expression("'hello'")  # 字符串顶层
    assert validate_expression("abs(-3)")  # 数值函数顶层
    assert validate_expression("date(2026,1,1)")  # 日期顶层
    # 含变量的算术顶层仍静态可知产出数值 → 报非布尔（单变量 {{x}} 才放行）
    assert validate_expression("{{x}} + 1")
    assert validate_expression("{{x}}") == []
    # 布尔函数/比较/逻辑顶层通过
    assert validate_expression("len('a') == 1") == []
    assert validate_expression("true") == []


# ---------- C（docs/27 §2）：可注入时钟 today()/now()/datetime()/hoursBetween ----------
import datetime as _dt

_FIXED = _dt.datetime(2026, 9, 19, 13, 30, tzinfo=_dt.timezone.utc)


def test_today_now_use_injected_clock_and_are_stable():
    # 注入固定时钟：today()/now() 取该时刻，且单次求值内确定
    assert evaluate_expression("today() == date(2026,9,19)", {}, now=_FIXED) is True
    assert evaluate_expression("now() == datetime(2026,9,19,13,30)", {}, now=_FIXED) is True
    assert evaluate_expression("now() >= datetime(2026,9,19,0,0)", {}, now=_FIXED) is True
    assert evaluate_expression("now() < datetime(2026,9,20,0,0)", {}, now=_FIXED) is True
    # year/month/day 可作用于 now()（datetime 是 date 子类）
    assert evaluate_expression(
        "year(now())==2026 && month(now())==9 && day(now())==19", {}, now=_FIXED
    ) is True
    # 不传时钟时取当前 UTC，today() 自比恒真（不报错即可）
    assert evaluate_expression("today() == today()", {}) is True


def test_datetime_constructor_and_hours_between():
    assert evaluate_expression(
        "hoursBetween(datetime(2026,9,19,10,0), datetime(2026,9,19,12,30)) == 2.5",
        {}, now=_FIXED,
    ) is True
    # 带秒的 6 参形式
    assert evaluate_expression(
        "hoursBetween(datetime(2026,9,19,10,0,0), datetime(2026,9,19,11,0,30)) == 1.0083333333333333",
        {}, now=_FIXED,
    ) is True
    # 反向为负
    assert evaluate_expression(
        "hoursBetween(datetime(2026,9,19,12,0), datetime(2026,9,19,9,0)) == -3",
        {}, now=_FIXED,
    ) is True


def test_hours_between_accepts_date_and_days_between_accepts_datetime():
    # date 按当日 00:00 UTC 入 hoursBetween
    assert evaluate_expression(
        "hoursBetween(date(2026,9,19), datetime(2026,9,19,6,0)) == 6", {}, now=_FIXED
    ) is True
    # daysBetween 接受 datetime（先取日期，再算整日差）
    assert evaluate_expression(
        "daysBetween(datetime(2026,9,19,23,0), datetime(2026,9,20,1,0)) == 1",
        {}, now=_FIXED,
    ) is True


def test_date_datetime_cross_type_ordered_compare_rejected():
    for expr in ("today() > now()", "date(2026,9,19) < now()", "now() >= date(2026,9,19)"):
        with pytest.raises(ConditionEvalError) as exc:
            evaluate_expression(expr, {}, now=_FIXED)
        assert "有序比较" in str(exc.value)
    # 严格相等不报错但为 False（date 与 datetime 不同类型）
    assert evaluate_expression("today() == now()", {}, now=_FIXED) is False
    assert evaluate_expression("today() != now()", {}, now=_FIXED) is True


def test_validate_datetime_functions():
    # 顶层裸 now()/today() 非布尔 → 报错
    assert any("布尔" in e for e in validate_expression("now()"))
    assert any("布尔" in e for e in validate_expression("today()"))
    # 合法的 now 比较通过
    assert validate_expression("now() >= datetime(2026,1,1,0,0)") == []
    # 非法日期时间常量折叠静态暴露（小时 25）
    assert any("非法日期时间" in e for e in validate_expression("datetime(2026,1,1,25,0) == now()"))
    # arity：datetime 需 5-6 参
    assert any("参数" in e for e in validate_expression("datetime(2026,1,1,0) == now()"))
    assert any("参数" in e for e in validate_expression("datetime(2026,1,1,0,0,0,0) == now()"))
    # hoursBetween 非日期时间参数
    errs = validate_expression("hoursBetween(1, now()) > 0")
    assert errs and any("hoursBetween" in e for e in errs)
    # datetime 分量必须整数
    assert any("整数" in e for e in validate_expression("datetime(2026.5,1,1,0,0) == now()"))
    # today/now 为 0 参
    assert any("参数" in e for e in validate_expression("today(1) == true"))


def test_condition_errors_carry_machine_code_and_params_docs60():
    """docs/60 G1：ConditionEvalError 除中文 message 外并行携带 code/params（英文类型码）。"""

    def raised(expr, ctx=None):
        with pytest.raises(ConditionEvalError) as exc:
            evaluate_expression(expr, ctx or {})
        return exc.value

    # 除零 / 模零
    assert raised("1/0").code == "COND_DIVIDE_BY_ZERO"
    assert raised("1/0").params["op"] == "/"
    assert raised("1 % 0").params["op"] == "%"

    # 算术类型不符（params 取左侧类型码）
    exc = raised("'a' + 1")
    assert exc.code == "COND_TYPE_MISMATCH"
    assert exc.params["op"] == "+"
    assert exc.params["expected"] == "number"
    assert exc.params["actual"] == "string"

    # 未知函数 / 未知标识符 / 缺右括号
    assert raised("foo(1)").code == "COND_UNKNOWN_FUNC"
    assert raised("foo(1)").params["func"] == "foo"
    exc = raised("bar")
    assert exc.code == "COND_UNKNOWN_IDENTIFIER"
    assert exc.params["token"] == "bar"
    assert raised("(1+2").code == "COND_SYNTAX_MISSING_PAREN"

    # 函数 arity
    exc = raised("len(1, 2)")
    assert exc.code == "COND_FUNC_ARITY"
    assert exc.params["func"] == "len"
    assert exc.params["actual"] == 2

    # null 参与有序比较
    assert raised("{{x}} > 1", {"x": None}).code == "COND_NULL_COMPARISON"

    # len 类型 / date 分量英文 token / 非法日期
    exc = raised("len(1)")
    assert exc.code == "COND_TYPE_MISMATCH"
    assert exc.params["func"] == "len"
    assert exc.params["expected"] == "string|array|object"
    exc = raised("date(2026, 'x', 1)")
    assert exc.code == "COND_TYPE_MISMATCH"
    assert exc.params["component"] == "month"  # 英文 token，不泄漏中文分量名
    assert raised("date(2026, 13, 40)").code == "COND_INVALID_DATE"

    # 中文 message 始终保留（兜底真相）
    assert "除数" in str(raised("1/0"))
