"""复核评审意见（prompt 版）的单测：用假 LLM，不连网。

为什么不用字符串匹配：评审意见是中文散文，机械比对照样「漏」或「假阳性」，
量出来的是用词像不像，不是有没有落点。这里测的是**复核这条链路的契约**：
prompt 里必须带上材料与逐条问题、解析必须严格（缺条就返回 None 而不猜）、
调用失败不能假装复核过。
"""

import asyncio

import pytest

from ai.agents.AiTaskPlatform.capabilities.core.evaluator import Evaluator


class _FakeLLM:
    """假 LLM：记下收到的 prompt，按顺序返回预设输出。"""

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def complete(self, prompt, system_prompt=None, max_tokens=0, temperature=0.0):
        self.calls.append({"prompt": prompt, "system": system_prompt})
        if not self.outputs:
            raise AssertionError("假 LLM 没准备好这么多次调用")
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def _verify(llm, issues, **kw):
    return asyncio.run(Evaluator.verify_issues(llm, issues, **kw))


def test_prompt_carries_material_and_every_issue():
    llm = _FakeLLM('{"checks": [{"index": 1, "grounded": true, "why": "ok"}]}')
    _verify(llm, ["回答漏了 E-7781"], draft="草稿正文", evidence="证据里有 E-7781", context="用户问根因")
    prompt = llm.calls[0]["prompt"]
    assert "用户问根因" in prompt and "证据里有 E-7781" in prompt and "草稿正文" in prompt
    assert "回答漏了 E-7781" in prompt and "1. " in prompt
    assert "只核对" in prompt, "口径必须写明不许评价回答质量"
    assert llm.calls[0]["system"] == Evaluator._VERIFY_SYSTEM_PROMPT


def test_empty_issues_skips_the_llm():
    llm = _FakeLLM()
    assert _verify(llm, []) == []
    assert llm.calls == [], "没有问题就不该调模型"


def test_parses_grounded_flags():
    llm = _FakeLLM(
        '{"checks": [{"index": 1, "grounded": true, "why": "证据里有"},'
        ' {"index": 2, "grounded": false, "why": "材料里没有『相机标定』"}]}'
    )
    got = _verify(llm, ["问题一", "问题二"])
    assert [c["grounded"] for c in got] == [True, False]
    assert "相机标定" in got[1]["why"]


def test_parses_fenced_json():
    llm = _FakeLLM('```json\n{"checks": [{"index": 1, "grounded": false, "why": "无"}]}\n```')
    assert _verify(llm, ["问题"])[0]["grounded"] is False


def test_missing_checks_or_garbage_returns_none():
    assert _verify(_FakeLLM("随便说点什么"), ["问题"]) is None
    assert _verify(_FakeLLM('{"checks": []}'), ["问题"]) is None
    assert _verify(_FakeLLM('{"checks": "不是数组"}'), ["问题"]) is None


def test_partial_checks_return_none_instead_of_guessing():
    """清单两条只回了一条 → 不能猜剩下那条，返回 None 让人去重跑。"""
    llm = _FakeLLM('{"checks": [{"index": 1, "grounded": true, "why": "x"}]}')
    assert _verify(llm, ["问题一", "问题二"]) is None


def test_llm_exception_returns_none():
    assert _verify(_FakeLLM(RuntimeError("boom")), ["问题"]) is None


def test_no_client_returns_none():
    assert asyncio.run(Evaluator.verify_issues(None, ["问题"])) is None


@pytest.mark.parametrize("raw,expected", [
    ('{"checks": [{"index": 1, "grounded": 1, "why": ""}]}', True),
    ('{"checks": [{"index": 1, "grounded": 0, "why": ""}]}', False),
])
def test_grounded_is_coerced_to_bool(raw, expected):
    assert _verify(_FakeLLM(raw), ["问题"])[0]["grounded"] is expected
