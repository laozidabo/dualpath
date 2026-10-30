from dualpath.models import Problem, classify_problems


def problem(title: str, probability: float) -> Problem:
    return Problem(
        id=title,
        title=title,
        description="",
        exploration="",
        probability=probability,
        reasoning="",
        basis="model_judgment",
    )


def test_highest_probability_is_always_level_one() -> None:
    problems = classify_problems([problem("低", 0.1), problem("最高", 0.4), problem("中", 0.2)])

    assert [item.title for item in problems] == ["最高", "中", "低"]
    assert [item.level for item in problems] == [1, 2, 3]


def test_equal_probabilities_have_equal_levels() -> None:
    problems = classify_problems(
        [problem("甲", 0.8), problem("乙", 0.8), problem("丙", 0.3)]
    )

    assert [item.level for item in problems] == [1, 1, 2]
