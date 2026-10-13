"""指标层单测：纯函数，不连网、不需要 LLM。

刻意**没有**「准确率」相关用例——这套量法不评判得对不对（标准随提问变），
只评判定稳定性、以及评审意见的落点（落点本身由 prompt 复核，这里只测汇总逻辑）。
"""

from ai.agents.AiTaskPlatform.eval import metrics


def _case(case_id, verdicts, issues=None, verifications=None):
    return {
        "case_id": case_id,
        "verdicts": list(verdicts),
        "issues": list(issues or []),
        "verifications": list(verifications or []),
        "context": "用户问：这单什么情况？",
        "evidence": "材料",
        "draft": "草稿正文",
    }


def test_count_verdicts_is_a_distribution_not_a_judgement():
    records = [_case("a", [True]), _case("b", [False]), _case("c", [None])]
    assert metrics.count_verdicts(records) == {"pass": 1, "fail": 1, "unparsed": 1}


def test_stability_flags_flipped_cases():
    records = [
        _case("stable", [True, True, True]),
        _case("flippy", [True, False, True]),
        _case("dead", [None, None]),
    ]
    st = metrics.stability(records)
    assert st["consistent"] == 1 and st["scored"] == 2
    assert st["rate"] == 0.5
    assert st["flipped"] == ["flippy"]
    assert st["unparsed_only"] == ["dead"]


def test_verify_report_collects_grounded_and_suspicious():
    records = [_case("a", [False, True], verifications=[
        [{"issue": "漏了 E-7781", "grounded": True, "why": "证据里有"},
         {"issue": "没提『相机标定』", "grounded": False, "why": "材料里没有"}],
        [],
    ])]
    vf = metrics.verify_report(records)
    assert (vf["grounded"], vf["not_grounded"]) == (1, 1)
    assert vf["rate"] == 0.5
    assert vf["suspicious"][0]["case_id"] == "a"
    assert vf["suspicious"][0]["round"] == 1
    assert "相机标定" in vf["suspicious"][0]["issue"]


def test_failed_verification_round_is_not_treated_as_ok():
    """复核没跑成不能算「落点没问题」——必须单列出来。"""
    records = [_case("a", [False], verifications=[None])]
    vf = metrics.verify_report(records)
    assert vf["failed_rounds"] == 1
    assert (vf["grounded"], vf["not_grounded"]) == (0, 0)
    assert vf["rate"] == 0.0


def test_verify_report_indices_rounds_across_all_rounds():
    records = [_case("a", [False, False], verifications=[
        [{"issue": "x", "grounded": True, "why": ""}],
        [{"issue": "y", "grounded": False, "why": ""}],
    ])]
    vf = metrics.verify_report(records)
    assert vf["suspicious"][0]["round"] == 2


def test_summarize_shape_and_empty_input():
    summary = metrics.summarize([_case("a", [True, True])])
    assert summary["cases"] == 1 and summary["rounds"] == 2
    assert summary["counts"]["pass"] == 2

    empty = metrics.summarize([])
    assert empty["cases"] == 0 and empty["rounds"] == 0
    assert empty["stability"]["rate"] == 0.0 and empty["verify"]["rate"] == 0.0


def test_format_report_has_no_accuracy_claim_and_says_when_nothing_checkable():
    records = [_case("a", [True, True])]
    text = metrics.format_report(metrics.summarize(records), records)
    assert "准确率" not in text and "误杀" not in text
    assert "多轮一致率" in text
    assert "无可复核的条目" in text, "没有可复核项时不能假装是个 0.0% 的比率"


def test_format_report_surfaces_failed_rounds_and_suspicious_items():
    records = [_case("a", [False], verifications=[
        [{"issue": "没提『相机标定』", "grounded": False, "why": "材料里没有『相机标定』"}]])]
    text = metrics.format_report(metrics.summarize(records), records)
    assert "相机标定" in text and "复核说明" in text

    failed = [_case("b", [False], verifications=[None])]
    text2 = metrics.format_report(metrics.summarize(failed), failed)
    assert "复核没跑成" in text2


def test_format_case_line_shows_issue_counts():
    line = metrics.format_case_line(_case("a", [False, True], issues=[["x", "y"], []]))
    assert "挑出问题数=[2, 0]" in line and "翻转" in line
