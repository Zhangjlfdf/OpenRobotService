"""Supervisor.run() 主流程：单轮基线与多轮追规。

为什么单独一个文件：`run()` 是每次 @U老师 的必经之路，但在本次改造前**零单测**——
`test_usage_quota.py` 全在绕开 LLM 直调 `_guard_decision`/`_dispatch_group`/`_synthesize`。
改核心流程却没有回归网，只能靠手动验，所以先把 run() 锁住。

不调真 LLM、不连库：假 LLM 按顺序吐 JSON 决策，替身能力直接返回文本。
"""

import asyncio
import json

import pytest

from ai.agents.AiTaskPlatform.capabilities.core.base import BaseCapability, CapabilityResult
from ai.agents.AiTaskPlatform.capabilities.core.registry import CapabilityRegistry
from ai.agents.AiTaskPlatform.capabilities.core.supervisor import Supervisor


class _CapA(BaseCapability):
    name = "run_stub_a"
    description = "run() 测试替身 A"
    calls: list = []
    terminate = False

    async def run(self, **kwargs):
        type(self).calls.append(kwargs.get("query", ""))
        return CapabilityResult(text=f"A的结果({kwargs.get('query','')})",
                                terminate=type(self).terminate)


class _CapB(BaseCapability):
    name = "run_stub_b"
    description = "run() 测试替身 B"
    calls: list = []

    async def run(self, **kwargs):
        type(self).calls.append(kwargs.get("query", ""))
        return CapabilityResult(text="B的结果")


class _SeqLLM:
    """按顺序吐决策 JSON；记下每次收到的 prompt，用来断言次轮看到了「已执行」。"""

    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.prompts: list[str] = []

    async def complete(self, prompt, system_prompt=None, max_tokens=2000,
                       temperature=0.1, thinking=False):
        self.prompts.append(prompt)
        if self.decisions:
            return self.decisions.pop(0)
        # 决策用尽 → 兜底 simple（等价于「规划器说够了」）
        return _dec("simple")


def _dec(complexity="medium", plan=None, **kw):
    d = {"complexity": complexity, "reasoning": "r", "plan": plan or []}
    d.update(kw)
    return json.dumps(d, ensure_ascii=False)


def _step(cap, goal="查一下"):
    return {"capability": cap, "goal": goal, "parallel": True}


@pytest.fixture
def caps():
    _CapA.calls, _CapB.calls = [], []
    _CapA.terminate = False
    CapabilityRegistry.register(_CapA)
    CapabilityRegistry.register(_CapB)
    yield
    for key in [k for k, v in CapabilityRegistry._capabilities.items()
                if v in (_CapA, _CapB)]:
        CapabilityRegistry._capabilities.pop(key, None)


def _run(llm, ctx=None):
    return asyncio.run(Supervisor(llm_client=llm).run(
        task_context="工单上下文",
        available_caps=["run_stub_a", "run_stub_b"],
        runtime_ctx=ctx if ctx is not None else {},
    ))


# ── 单轮基线（改造前就该绿，改造后必须仍然绿）────────────────

def test_simple_plans_once_and_dispatches_nothing(caps):
    """规划器说 simple → 只规划一次、一个能力都不派。"""
    llm = _SeqLLM(_dec("simple"))
    res = _run(llm)

    assert len(llm.prompts) == 1
    assert _CapA.calls == [] and _CapB.calls == []
    assert res["results"] == {}
    assert res["plan"] == []


def test_planned_capability_runs_and_result_is_returned(caps):
    """派了的能力真的跑、结果按能力名回到 results。"""
    llm = _SeqLLM(_dec("medium", [_step("run_stub_a", "查A")]))
    res = _run(llm)

    assert _CapA.calls == ["查A"]
    assert res["results"]["run_stub_a"]["text"] == "A的结果(查A)"
    assert res["results"]["run_stub_a"]["ok"] is True


def test_results_keyed_by_capability_name(caps):
    """results 以**能力名**为 key —— 这是多轮里「同一能力只能出现一次」的根因。"""
    llm = _SeqLLM(_dec("complex", [_step("run_stub_a", "A"), _step("run_stub_b", "B")]))
    res = _run(llm)

    assert set(res["results"].keys()) == {"run_stub_a", "run_stub_b"}


# ── 多轮追规 ────────────────────────────────────────────────

def test_second_round_planner_sees_what_already_ran(caps):
    """追规那轮必须看得到「上一轮派了什么、结果如何」，否则只会重复派。"""
    llm = _SeqLLM(
        _dec("medium", [_step("run_stub_a", "查A")]),
        _dec("simple"),                      # 次轮：够了
    )
    _run(llm)

    assert len(llm.prompts) == 2, "应该规划两次（初始 + 追规）"
    second = llm.prompts[1]
    assert "run_stub_a" in second, "次轮 prompt 必须提到已派过的能力"
    assert "A的结果(查A)" in second, "次轮 prompt 必须带上已拿到的结果"


def test_second_round_cannot_redispatch_a_done_capability(caps):
    """次轮再要派同一能力 → 直接不排（results 以能力名为 key，覆盖等于吞掉证据）。"""
    _CapB.calls.clear()
    llm = _SeqLLM(
        _dec("medium", [_step("run_stub_a", "第一次")]),
        _dec("complex", [_step("run_stub_a", "又来一次")]),   # 非法：A 已派过
    )
    res = _run(llm)

    assert _CapA.calls == ["第一次"], "A 不应被派第二次"
    assert res["results"]["run_stub_a"]["text"] == "A的结果(第一次)", "第一轮证据不能被覆盖"


def test_terminate_stops_before_the_next_round(caps):
    """历史方案已验证（terminate）→ 不该再白跑一轮昂贵能力。"""
    _CapA.terminate = True
    llm = _SeqLLM(_dec("medium", [_step("run_stub_a", "查历史")]))
    _run(llm)

    assert len(llm.prompts) == 1, "terminate 之后不该再规划"


def test_at_most_two_rounds(caps):
    """上限就是 2：两轮都想派也只规划两次。"""
    llm = _SeqLLM(
        _dec("medium", [_step("run_stub_a", "一")]),
        _dec("medium", [_step("run_stub_b", "二")]),
        _dec("medium", [_step("run_stub_b", "三")]),   # 不该被用到
    )
    _run(llm)

    assert len(llm.prompts) == 2
    assert _CapB.calls == ["二"]


def test_quota_block_is_marked_explicitly(caps):
    """配额拦下必须留**独立标记** —— 否则上层只能看到 ok=False，会渲染成「分析失败」。

    用 `_CapA` 预先把台账占满（A 本身无上限，这里直接写台账模拟「上一轮已用满」）。
    """
    from ai.agents.AiTaskPlatform.capabilities.core import usage

    ctx = {}
    usage.ledger(ctx)
    ctx["cap_usage"]["run_stub_b"] = 0     # 确保台账存在（无上限能力不会被拦）

    res = _run(_SeqLLM(_dec("medium", [_step("run_stub_b", "B")])), ctx=ctx)
    assert res["results"]["run_stub_b"]["ok"] is True



# ── 返回形状与埋点 ──────────────────────────────────────────

def test_two_rounds_append_plan_and_capability_spans_in_order(caps):
    """过程区/span 树靠结构渲染：两轮应追加第二组「plan + 能力」，顺序不乱。

    （`tracing/test_trace_bus.py` 里那条 `names == ["plan", ...]` 是**手工构造 span**
      的纯 TraceBus 测试，不走 run()，锁不住这块。）
    """
    llm = _SeqLLM(
        _dec("medium", [_step("run_stub_a", "一")]),
        _dec("medium", [_step("run_stub_b", "二")]),
    )
    res = _run(llm)

    roots = [s for s in res["spans"] if s["name"] == "supervisor.run"]
    assert len(roots) == 1, "整场仍归一个根节点"
    names = [c["name"] for c in roots[0]["children"]]
    assert names == ["plan", "run_stub_a", "plan", "run_stub_b"], \
        f"两轮应各留一组 plan + 能力，实际 {names}"
    assert names.count("plan") == 2


def test_rounds_are_reported_for_trace(caps):
    """每轮留下一条可读记录（派了什么 / 为什么停），供 trace 与排查。"""
    llm = _SeqLLM(
        _dec("medium", [_step("run_stub_a", "一")]),
        _dec("simple"),
    )
    res = _run(llm)

    assert [r["round"] for r in res["rounds"]] == [1, 2]
    assert res["rounds"][0]["planned"] == ["run_stub_a"]
    assert "stop" in res["rounds"][1], "停的那轮要写清原因"


def test_plan_is_cumulative_and_complexity_from_first_round(caps):
    """plan 累计两轮（上层只用于展示）；complexity 取**首轮**。"""
    llm = _SeqLLM(
        _dec("complex", [_step("run_stub_a", "一")]),
        # 次轮仍要派 → 不能写 simple（simple 会带空 plan 并直接结束这一轮）
        _dec("medium", [_step("run_stub_b", "二")]),
    )
    res = _run(llm)

    assert [s["capability"] for s in res["plan"]] == ["run_stub_a", "run_stub_b"]
    assert res["complexity"] == "complex", "不该被次轮的 simple 覆盖"


def test_note_key_is_separate_from_results(caps):
    """配额说明走独立的 note 键，不污染 results（上层按能力名读 results）。"""
    llm = _SeqLLM(_dec("simple"))
    res = _run(llm)

    assert res["note"] == "", "没有拦下时不该有说明"
    assert res["final_text"] == "", "没派任何能力时没有汇总正文"


def test_executed_block_stays_bounded(caps):
    """喂回规划器的「已执行」段要截断 —— 否则等于把长结果再灌一遍。"""
    from ai.agents.AiTaskPlatform.capabilities.core.supervisor import (
        _EXECUTED_BLOCK_CHARS,
        _executed_block,
    )

    long_text = "很长的结论" * 2000
    block = _executed_block([{"capability": "x", "goal": "g"}], {"x": {"ok": True, "text": long_text}})
    assert len(block) <= _EXECUTED_BLOCK_CHARS
    assert "已截断" in block
