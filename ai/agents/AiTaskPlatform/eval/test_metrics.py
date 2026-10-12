"""指标层单测：纯函数，不连网、不需要 LLM。"""

from ai.agents.AiTaskPlatform.eval import metrics


def _case(case_id, expected, verdicts):
    return {"case_id": case_id, "expected": expected, "verdicts": list(verdicts)}


def test_confusion_splits_mistake_directions():
    """误杀与漏放必须分开——后果不同，糊成一个准确率就没法判断该改哪边。"""
    records = [
        _case("ok", True, [True, True]),
        _case("killed", True, [False]),          # 误杀
        _case("leaked", False, [True]),          # 漏放
        _case("rejected", False, [False]),
    ]
    c = metrics.confusion(records)
    assert c == {"hit": 3, "false_reject": 1, "false_accept": 1, "comparable": 5}


def test_unparsed_is_counted_but_kept_out_of_accuracy():
    """判官没跑起来 ≠ 判定结果：不能算进分母，也不能美化成通过/不通过。"""
    records = [_case("a", True, [True]), _case("b", True, [None])]
    assert metrics.count_verdicts(records) == {"pass": 1, "fail": 0, "unparsed": 1}
    acc = metrics.accuracy(records)
    assert acc["comparable"] == 1 and acc["rate"] == 1.0


def test_accuracy_by_round_isolates_first_round():
    """首轮单独可看：翻转带来的差异才定位得出来。"""
    records = [_case("a", True, [True, False, True])]
    assert metrics.accuracy(records)["rate"] == 2 / 3
    assert metrics.accuracy(records, round_idx=0)["rate"] == 1.0


def test_stability_flags_flipped_cases():
    """翻转用例要点名列出：这说明判据对这类输入不可靠，而不是模型偶尔手滑。"""
    records = [
        _case("stable", True, [True, True, True]),
        _case("flippy", True, [True, False, True]),
        _case("dead", False, [None, None]),
    ]
    st = metrics.stability(records)
    assert st["consistent"] == 1 and st["scored"] == 2
    assert st["rate"] == 0.5
    assert st["flipped"] == ["flippy"]
    assert st["unparsed_only"] == ["dead"]


def test_summarize_shape_and_empty_input():
    summary = metrics.summarize([_case("a", True, [True, True])])
    assert summary["cases"] == 1 and summary["rounds"] == 2
    assert summary["overall"]["rate"] == 1.0

    empty = metrics.summarize([])
    assert empty["cases"] == 0 and empty["rounds"] == 0
    assert empty["overall"]["rate"] == 0.0 and empty["stability"]["rate"] == 0.0


def test_format_case_line_marks_mistakes_and_flips():
    line = metrics.format_case_line(_case("x", True, [True, False]))
    assert "翻转" in line
    assert "❌ 判错" in metrics.format_case_line(_case("y", True, [False, False]))
    assert "❌" not in metrics.format_case_line(_case("z", True, [True, True]))


def test_format_report_puts_the_actionable_bits_up_front():
    records = [_case("killed-one", True, [False, False]),
               _case("flipped-one", False, [True, False])]
    text = metrics.format_report(metrics.summarize(records), records)
    assert "误杀" in text and "漏放" in text
    assert "翻转用例" in text and "flipped-one" in text
    assert text.index("准确率") < text.index("用例明细")
