# -*- coding: utf-8 -*-
"""pipeline 车辆定制模式单测（XQE 试点，阶段 2）。

核心断言：常规会话（无 vehicle_mode 键）路径零变化——
- _vehicle_mode_block 返回空串
- _vehicle_mode_domains 返回 None
- _three_way_retrieve 默认走 team/company/industry 三域（域参数逐字节一致）
定制会话：
- 【车辆】块注入 session_state / 收集 / 快路径
- 检索域换成车型域优先配额
"""
import pytest

from ai.agents.AiDiagnosisPlatform.pipeline import (
    AiDiagnosisPlatform,
    _session_state_block,
    _vehicle_mode_block,
)
from ai.core.memory import SessionMemory


def _memory(vm=None, draft=None):
    m = SessionMemory(session_id="s-vm")
    if vm is not None:
        m.metadata["vehicle_mode"] = vm
    if draft is not None:
        m.metadata["ticket_draft"] = draft
    return m


_VM = {
    "model": "XQE", "domain": "xqe", "vehicle_code": "XQE-122",
    "project_name": "试点项目", "customer_name": "试点客户",
}


# ================================================================
# _vehicle_mode_block
# ================================================================

def test_vehicle_block_empty_for_normal_session():
    # 常规会话（无 vehicle_mode）→ 空串零注入
    assert _vehicle_mode_block(_memory()) == ""
    assert _vehicle_mode_block(_memory({})) == ""


def test_vehicle_block_renders_fields():
    out = _vehicle_mode_block(_memory(_VM))
    assert "【车辆】" in out
    assert "车型 XQE" in out
    assert "车号 XQE-122" in out
    assert "试点项目" in out
    assert "不要向用户追问" in out
    # 0930 选项引导规则
    assert "vehicle_choices" in out
    assert "不得编造" in out


def test_vehicle_block_last_choices():
    # 上轮已出选项未答：注入不重复出题 + 原文还原规则
    vm = dict(_VM, last_choices=["行驶途中停下", "在取货位停住"])
    out = _vehicle_mode_block(_memory(vm))
    assert "上一轮你已给出选项" in out
    assert "不要重复给出选项" in out
    assert "行驶途中停下" in out
    # 无 last_choices 不注入该段
    assert "上一轮你已给出选项" not in _vehicle_mode_block(_memory(_VM))


def test_vehicle_block_minimal_fields():
    out = _vehicle_mode_block(_memory({"model": "XQE", "domain": "xqe"}))
    assert "车型 XQE" in out
    assert "车号" not in out
    assert "项目" not in out


# ================================================================
# _vehicle_mode_domains（检索域配额）
# ================================================================

async def test_domains_none_for_normal_session():
    p = AiDiagnosisPlatform()
    p._memory_manager = MagicMockMem(_memory())
    assert await p._vehicle_mode_domains("s-vm") is None


async def test_domains_vehicle_filtered_company():
    p = AiDiagnosisPlatform()
    p._memory_manager = MagicMockMem(_memory(_VM))
    domains = await p._vehicle_mode_domains("s-vm")
    # 0930 定稿：company 域 + sub_domain 精确过滤（车型知识挂 company/{车型}/，
    # 不建独立域）——两重隔离：不捞通用域老知识，也不捞 company 其他产品线内容
    assert len(domains) == 1
    assert domains[0][0] == "company" and domains[0][1] == 8
    qfilter = domains[0][2]
    assert qfilter is not None
    cond = qfilter.must[0]
    assert cond.key == "sub_domain" and cond.match.value == "XQE/manual"


async def test_domains_none_on_memory_error():
    p = AiDiagnosisPlatform()

    class Boom:
        async def get_memory(self, sid):
            raise RuntimeError("redis down")

    p._memory_manager = Boom()
    # 读取失败按常规处理（不阻塞检索主链路）
    assert await p._vehicle_mode_domains("s-vm") is None


class MagicMockMem:
    """最小 MemoryManager 桩：只实现 get_memory。"""

    def __init__(self, mem):
        self._mem = mem
        self.max_turns = 20

    async def get_memory(self, session_id):
        return self._mem

    async def save_memory(self, mem):
        pass


# ================================================================
# _three_way_retrieve 域参数化
# ================================================================

def _platform_with_retriever(capture):
    """capture: list，记录 retrieve_domain_dual 的 (query, domain, filter) 调用。"""
    p = AiDiagnosisPlatform()
    retr = type("R", (), {})()

    async def retrieve_domain_dual(query, domain, top_k=8, query_filter=None):
        capture.append((query, domain, query_filter))
        return [], []

    retr.retrieve_domain_dual = retrieve_domain_dual
    p._retriever = retr
    return p


async def test_three_way_default_domains_unchanged():
    """常规调用（domains=None）→ 仍是 team/company/industry 三域，行为不变。"""
    capture = []
    p = _platform_with_retriever(capture)
    await p._three_way_retrieve("测试查询")
    assert [(d, f) for _, d, f in capture] == [
        ("team", None), ("company", None), ("industry", None)]


async def test_three_way_custom_domains():
    """定制模式传入域配额 → 按传入的域检索（二元不带 filter / 三元带 filter）。"""
    capture = []
    p = _platform_with_retriever(capture)
    _f = object()
    await p._three_way_retrieve(
        "货叉不动", domains=[("company", 8, _f), ("team", 2)])
    assert [(d, f) for _, d, f in capture] == [("company", _f), ("team", None)]


async def test_three_way_custom_domain_exception_tolerated():
    """车型域检索异常 → 降级空列表，不炸主链路。"""
    p = AiDiagnosisPlatform()
    retr = type("R", (), {})()

    async def dual(query, domain, top_k=8, query_filter=None):
        if domain == "company":
            raise TimeoutError("company timeout")
        return [], []

    retr.retrieve_domain_dual = dual
    p._retriever = retr
    results = await p._three_way_retrieve("q", domains=[("company", 8), ("team", 2)])
    assert results == []


# ================================================================
# _session_state_block 集成
# ================================================================

def test_session_state_includes_vehicle(make_state):
    state = make_state()
    out = _session_state_block(state, _memory(_VM))
    assert "【车辆】" in out
    assert "XQE-122" in out


def test_session_state_normal_no_vehicle(make_state):
    state = make_state()
    out = _session_state_block(state, _memory())
    assert "【车辆】" not in out


# ================================================================
# vehicle_choices 校验 + last_choices 记账（0930 选项引导）
# ================================================================

def test_validate_vehicle_choices_ok():
    p = AiDiagnosisPlatform()
    vc = p._validate_vehicle_choices(
        {"vehicle_choices": ["行驶途中停下", "在取货位停住"]})
    assert vc == ["行驶途中停下", "在取货位停住"]
    # 前后空白清洗
    assert p._validate_vehicle_choices({"vehicle_choices": [" a ", "b"]}) == ["a", "b"]


def test_validate_vehicle_choices_rejects():
    # 整体丢弃制：任一不满足 → None（当普通回复处理）
    p = AiDiagnosisPlatform()
    bad = [
        None, [],                       # 非法/空
        ["只有一项"],                    # <2
        ["a", "b", "c", "d"],           # >3
        ["a", 123],                     # 非字符串元素
        ["a", "   "],                   # 空串
        ["x" * 31, "y"],                # 单条超 30 字
        ["同", "同"],                    # 去重变少
    ]
    for vc in bad:
        assert p._validate_vehicle_choices({"vehicle_choices": vc}) is None, vc


async def test_finalize_last_choices_accounting(monkeypatch):
    """定制会话：出题轮覆盖记录 last_choices；未出题轮清除（防陈旧选项）。"""
    p = AiDiagnosisPlatform()
    p._memory_manager = MagicMockMem(_memory(_VM))
    monkeypatch.setattr(p, "_cleanup_kb_image_urls", lambda m: m)
    monkeypatch.setattr(p, "_strip_unknown_kb_images", lambda m, sid: m)
    from ai.agents.AiDiagnosisPlatform.pipeline import AgentState
    state = AgentState(session_id="s-vm", phase="diagnosing", problem_summary="t")
    state.diagnosis_rounds = 0
    await p._finalize_diagnosis("s-vm", state, "", "ask", "好的",
                                vehicle_choices=["行驶途中停下", "在取货位停住"])
    mem = await p._memory_manager.get_memory("s-vm")
    assert mem.metadata["vehicle_mode"]["last_choices"] == ["行驶途中停下", "在取货位停住"]
    await p._finalize_diagnosis("s-vm", state, "", "ask", "好的", vehicle_choices=None)
    assert "last_choices" not in mem.metadata["vehicle_mode"]


async def test_finalize_normal_session_never_writes_vm(monkeypatch):
    """铁律：常规会话（无 vehicle_mode）即使 LLM 幻觉出选项也不写任何车辆键。"""
    p = AiDiagnosisPlatform()
    mem0 = _memory()
    p._memory_manager = MagicMockMem(mem0)
    monkeypatch.setattr(p, "_cleanup_kb_image_urls", lambda m: m)
    monkeypatch.setattr(p, "_strip_unknown_kb_images", lambda m, sid: m)
    from ai.agents.AiDiagnosisPlatform.pipeline import AgentState
    state = AgentState(session_id="s-vm", phase="diagnosing", problem_summary="t")
    state.diagnosis_rounds = 0
    await p._finalize_diagnosis("s-vm", state, "", "ask", "好的",
                                vehicle_choices=["a", "b"])
    assert "vehicle_mode" not in mem0.metadata
