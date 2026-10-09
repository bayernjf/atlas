"""打包 AF（docs/112）Part 1：choice / weightedChoice —— U1293–U1299。"""

import random

import pytest

from atlas.graph.conditions import ConditionEvalError, evaluate_expression


def eval_with(expr, seed):
    return evaluate_expression(expr, {}, rng=random.Random(seed))


# U1293：choice 均匀随机
def test_u1293_choice_single_argument_returns_itself():
    assert eval_with('choice("x")', 1) == "x"
    assert eval_with("choice(7)", 1) == 7


def test_u1293_choice_result_in_set_and_roughly_uniform():
    rng = random.Random(123)
    results = [evaluate_expression('choice("a","b","c")', {}, rng=rng) for _ in range(3000)]
    assert set(results) == {"a", "b", "c"}
    counts = {v: results.count(v) for v in "abc"}
    for v in "abc":
        assert 0.28 < counts[v] / len(results) < 0.39  # 期望 ~1/3


# U1294：choice 同类型 / 参数
def test_u1294_choice_mixed_types_rejected():
    with pytest.raises(ConditionEvalError, match="同一类型") as exc:
        eval_with('choice("a", 1)', 1)
    assert exc.value.code == "COND_TYPE_MISMATCH"


def test_u1294_choice_mixed_date_and_string_rejected():
    with pytest.raises(ConditionEvalError) as exc:
        eval_with('choice(date(2026,1,1), "x")', 1)
    assert exc.value.code == "COND_TYPE_MISMATCH"


def test_u1294_choice_zero_arguments_arity_error():
    with pytest.raises(ConditionEvalError) as exc:
        eval_with("choice()", 1)
    assert exc.value.code == "COND_FUNC_ARITY"


# U1295：weightedChoice 按权重
def test_u1295_weighted_choice_ratio():
    rng = random.Random(99)
    results = [
        evaluate_expression('weightedChoice("a", 9, "b", 1)', {}, rng=rng)
        for _ in range(4000)
    ]
    assert set(results) <= {"a", "b"}
    ratio_a = results.count("a") / len(results)
    assert 0.85 < ratio_a < 0.95


def test_u1295_weighted_choice_returns_item_not_weight():
    rng = random.Random(5)
    for _ in range(50):
        value = evaluate_expression("weightedChoice(10, 1, 20, 1)", {}, rng=rng)
        assert value in (10, 20)


# U1296：weightedChoice 参数形状
def test_u1296_odd_arguments_rejected():
    with pytest.raises(ConditionEvalError, match="成对") as exc:
        eval_with('weightedChoice("a", 9, "b")', 1)
    assert exc.value.code == "COND_TYPE_MISMATCH"


def test_u1296_too_few_arguments_arity_error():
    with pytest.raises(ConditionEvalError) as exc:
        eval_with('weightedChoice("a")', 1)
    assert exc.value.code == "COND_FUNC_ARITY"


# U1297：权重校验
def test_u1297_non_numeric_weight_rejected():
    with pytest.raises(ConditionEvalError) as exc:
        eval_with('weightedChoice("a", "x", "b", 1)', 1)
    assert exc.value.code == "COND_TYPE_MISMATCH"


def test_u1297_boolean_weight_rejected():
    with pytest.raises(ConditionEvalError) as exc:
        eval_with('weightedChoice("a", true, "b", 1)', 1)
    assert exc.value.code == "COND_TYPE_MISMATCH"


def test_u1297_negative_weight_rejected():
    with pytest.raises(ConditionEvalError, match="不能为负") as exc:
        eval_with('weightedChoice("a", -1, "b", 1)', 1)
    assert exc.value.code == "COND_TYPE_MISMATCH"


def test_u1297_all_zero_weights_rejected():
    with pytest.raises(ConditionEvalError, match="总和必须大于 0") as exc:
        eval_with('weightedChoice("a", 0, "b", 0)', 1)
    assert exc.value.code == "COND_INVALID_RANGE"


def test_u1297_zero_weight_item_never_chosen():
    rng = random.Random(7)
    for _ in range(200):
        assert evaluate_expression('weightedChoice("a", 1, "b", 0)', {}, rng=rng) == "a"


# U1298：种子化回放
def test_u1298_same_seed_same_sequence():
    def run(seed):
        rng = random.Random(seed)
        return [evaluate_expression('choice("a","b","c","d")', {}, rng=rng) for _ in range(20)]

    assert run(2026) == run(2026)


def test_u1298_weighted_same_seed_same_sequence():
    def run(seed):
        rng = random.Random(seed)
        return [
            evaluate_expression('weightedChoice("a", 3, "b", 7)', {}, rng=rng)
            for _ in range(20)
        ]

    assert run(555) == run(555)


def test_u1298_different_seed_can_differ():
    def run(seed):
        rng = random.Random(seed)
        return [evaluate_expression('choice("a","b","c","d")', {}, rng=rng) for _ in range(15)]

    # 不同种子不要求必然不同，但极大概率不同；断言至少存在差异（4 项 15 次）。
    assert run(1) != run(2)


# U1299：weightedChoice item 同类型
def test_u1299_items_mixed_types_rejected():
    with pytest.raises(ConditionEvalError) as exc:
        eval_with('weightedChoice("a", 1, 2, 1)', 1)
    assert exc.value.code == "COND_TYPE_MISMATCH"
