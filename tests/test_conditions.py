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
    assert validate_expression("1 + 1")  # 算术不支持，残留 token 报错
    assert validate_expression("   ") == ["表达式不能为空"]


def test_validate_literal_type_error_is_static():
    errors = validate_expression("'a' > 1")
    assert errors and "同为数字或同为字符串" in errors[0]
    # 含变量的类型错误无法静态发现，校验期放行（运行时 fail-safe）
    assert validate_expression("{{name}} > 1") == []


def test_valid_expressions_pass_validation():
    assert validate_expression("{{trigger-1.context.payload.amount}} > 1000 && {{status}} == 'paid'") == []
    assert validate_expression("!({{x}} == null) || {{y}} >= -10") == []
