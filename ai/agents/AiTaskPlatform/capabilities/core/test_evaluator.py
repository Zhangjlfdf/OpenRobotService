"""Evaluator 自评契约：evidence 必须覆盖全部事实来源 + 判据不绑定诊断场景。

回归背景：discuss 路径曾只把 facultative（附件分析）当 evidence 传给自评，
而讨论类回答的事实来自讨论历史，于是被逐字判成「幻构」——
    "幻构：证据明确说明当前工单没有日志、图片或任何可解析的附件，
     回答却编造了具体人名（甲、乙）…"
随后按「修正评审问题」把正确内容删掉（实测旧重写 prompt 会删掉全部人名）。
本文件锁住这三处修复，均为纯逻辑断言，不调用 LLM。
注：人名/ID 全为合成占位符。
"""
import asyncio
import sys

from ai.agents.AiTaskPlatform.capabilities.core.evaluator import Evaluator

# ---------------------------------------------------------------- evidence 拼装


def test_build_evidence_keeps_discussion_history_facts():
    """讨论历史必须能进 evidence：它常是讨论类回答的唯一事实来源。"""
    ev = Evaluator.build_evidence(
        "甲：要不要针对小屏优化？",
        "乙：不用额外优化。",
    )
    assert "甲：" in ev
    assert "乙：" in ev


def test_build_evidence_skips_empty_blocks():
    """空/纯空白/None 块不参与拼装，避免 evidence 里出现无意义空行。"""
    assert Evaluator.build_evidence("有内容", "", "   ", None, "尾部") == "有内容\n\n尾部"


def test_build_evidence_truncates_to_limit():
    assert len(Evaluator.build_evidence("x" * 5000, limit=100)) == 100


def test_build_evidence_empty_all():
    assert Evaluator.build_evidence("", None) == ""


# ---------------------------------------------------------------- 自评判据


def _criteria(prompt: str) -> str:
    """截取判据部分（排除后面的 context/evidence/draft 拼接内容）。"""
    return prompt.split("判定口径")[0]


def test_eval_prompt_judges_by_question_not_evidence():
    """判据必须以「用户问题要什么」为准，否则正确回答会被判漏关键信息（回归核心）。

    实测：回答忠实答了用户问的那一点，自评却因回答未提及附件里的另一主题而判
    「漏关键信息」，4/4 不通过，重写后正确回答被改成偏题。
    """
    prompt = Evaluator._build_eval_prompt("草稿", "证据", "上下文")
    assert "不是「证据里有什么」" in prompt
    assert "不得要求回答补齐" in prompt


def test_discuss_mode_drops_completeness_criterion():
    """讨论场景不得评估「漏关键信息」：用户只问一点时，该判据会把正确回答判成偏题。"""
    criteria = _criteria(
        Evaluator._build_eval_prompt("草稿", "证据", "上下文", mode="discuss")
    )
    assert "漏关键信息" not in criteria
    assert "偏题" in criteria
    assert "幻构" in criteria


def test_diagnose_mode_keeps_completeness_criterion():
    """诊断报告仍要评完整性：漏掉证据指向的根因是真问题。"""
    criteria = _criteria(
        Evaluator._build_eval_prompt("草稿", "证据", "上下文", mode="diagnose")
    )
    assert "漏关键信息" in criteria


def test_default_mode_is_diagnose():
    """不传 mode 时保持原有诊断口径，避免影响既有 diagnose 路径。"""
    assert Evaluator._build_eval_prompt("d", "e", "c") == Evaluator._build_eval_prompt(
        "d", "e", "c", mode="diagnose"
    )


def test_both_modes_share_judge_rules():
    """判定口径（以问题为准、已出现不算幻构等）两种场景都必须有。"""
    for mode in ("diagnose", "discuss"):
        prompt = Evaluator._build_eval_prompt("草稿", "证据", "上下文", mode=mode)
        assert "不是「证据里有什么」" in prompt
        assert "已经出现的内容一律不算幻构" in prompt


def test_eval_criteria_not_diagnosis_specific():
    """4 条判据不得绑定诊断场景口径，否则讨论类问题（无错误码）天然无解。"""
    criteria = _criteria(Evaluator._build_eval_prompt("草稿", "证据", "上下文"))
    assert "错误码" not in criteria
    assert "车型" not in criteria
    assert "根因" not in criteria


def test_eval_prompt_judges_by_evidence_only():
    """必须写明「证据里已出现的不算幻构」，否则真实事实会被判成编造。"""
    prompt = Evaluator._build_eval_prompt("草稿", "证据", "上下文")
    assert "已经出现的内容一律不算幻构" in prompt


def test_eval_prompt_ignores_imperative_notice():
    """facultative 告知语（“不要编造”）是给生成方的约束，不是待答内容。"""
    prompt = Evaluator._build_eval_prompt("草稿", "证据", "上下文")
    assert "不是待回答的内容" in prompt


def test_eval_prompt_output_contract_unchanged():
    """JSON 输出契约不能变：_parse_eval 依赖 pass/issues/reasoning 三个键。"""
    prompt = Evaluator._build_eval_prompt("草稿", "证据", "上下文")
    assert '"pass": true, "issues": [], "reasoning"' in prompt
    assert '"pass": false, "issues"' in prompt


# ---------------------------------------------------------------- 重写保护


def test_rewrite_prompt_protects_evidence_backed_facts():
    """重写不得删除证据支持的事实，否则正确回答会被改没（回归核心）。"""
    prompt = Evaluator._build_rewrite_prompt(
        "原回答", ["幻构：人名不存在"], "证据", "上下文",
    )
    assert "必须保留" in prompt
    assert "不要迁就该问题" in prompt
    assert "不要新增证据之外的事实" in prompt


def test_rewrite_prompt_still_keeps_length_constraint():
    """讨论区篇幅约束保持原样，修复不应改变既有风格要求。"""
    prompt = Evaluator._build_rewrite_prompt("原回答", [], "证据", "上下文")
    assert "只回复重点" not in prompt  # 该约束属于生成 prompt，不属于重写 prompt
    assert "只要重点 1～2 句话" in prompt
    assert "（无具体问题）" in prompt


# ---------------------------------------------------------------- JSON 解析


def test_parse_eval_accepts_fenced_json():
    data = Evaluator._parse_eval(
        '```json\n{"pass": true, "issues": [], "reasoning": "ok"}\n```'
    )
    assert data["pass"] is True


def test_parse_eval_accepts_prose_wrapped_json():
    data = Evaluator._parse_eval('评估结果如下：{"pass": false, "issues": ["偏题"]}')
    assert data["pass"] is False
    assert data["issues"] == ["偏题"]


def test_parse_eval_rejects_malformed():
    assert Evaluator._parse_eval("完全不是 JSON") is None
    assert Evaluator._parse_eval('{"issues": []}') is None
    assert Evaluator._parse_eval("") is None


# ---------------------------------------------------------------- 改写开关


class _FakeLLM:
    """按调用顺序返回预设回复的假 LLM，并记录调用次数。"""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        return self.replies[min(self.calls - 1, len(self.replies) - 1)]


_FAIL_JSON = (
    '{"pass": false, "issues": ["结论与证据矛盾：只提小屏适配"], "reasoning": "x"}'
)


def test_discuss_mode_records_verdict_but_keeps_draft():
    """讨论场景：自评判不通过也不得覆盖回答（正确回答曾被改成偏题）。"""
    fake = _FakeLLM(_FAIL_JSON)
    res = asyncio.run(Evaluator.evaluate_and_rewrite(
        fake, "主要分歧是小屏适配", "证据", "上下文", mode="discuss"))
    assert res["final"] == "主要分歧是小屏适配"   # 初稿原样保留
    assert res["rewritten"] is False
    assert res["advisory"] is True
    assert res["eval_notes"] == ["结论与证据矛盾：只提小屏适配"]
    assert fake.calls == 1                       # 不再多打一次重写调用


def test_diagnose_mode_still_rewrites():
    """诊断报告必须保留重写能力，不能因讨论场景的修复而退化。"""
    fake = _FakeLLM(_FAIL_JSON, "修正后的报告")
    res = asyncio.run(Evaluator.evaluate_and_rewrite(
        fake, "原报告", "证据", "上下文", mode="diagnose"))
    assert res["rewritten"] is True
    assert res["final"] == "修正后的报告"
    assert res["advisory"] is False
    assert fake.calls == 2


def test_pass_verdict_is_not_advisory():
    fake = _FakeLLM('{"pass": true, "issues": [], "reasoning": "ok"}')
    res = asyncio.run(Evaluator.evaluate_and_rewrite(
        fake, "初稿", "证据", "上下文", mode="discuss"))
    assert res["final"] == "初稿"
    assert res["advisory"] is False
    assert fake.calls == 1


def test_all_return_paths_expose_advisory_key():
    """返回契约统一：调用方无需 .get() 兜底。"""
    fake_fail_call = _FakeLLM("不是 JSON")
    res = asyncio.run(Evaluator.evaluate_and_rewrite(
        fake_fail_call, "初稿", "证据", "上下文"))
    assert "advisory" in res
    assert res["eval_failed"] is True
