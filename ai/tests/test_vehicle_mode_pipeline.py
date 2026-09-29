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
    assert "编号选项" in out


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


async def test_domains_xqe_priority_for_custom_session():
    p = AiDiagnosisPlatform()
    p._memory_manager = MagicMockMem(_memory(_VM))
    domains = await p._vehicle_mode_domains("s-vm")
    assert domains[0] == ("xqe", 8)  # 车型域最高配额
    names = [d for d, _ in domains]
    assert names == ["xqe", "team", "company", "industry"]  # 通用域兜底保留


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

    async def get_memory(self, session_id):
        return self._mem


# ================================================================
# _three_way_retrieve 域参数化
# ================================================================

def _platform_with_retriever(capture):
    """capture: list，记录 retrieve_domain_dual 的 (query, domain) 调用。"""
    p = AiDiagnosisPlatform()
    retr = type("R", (), {})()

    async def retrieve_domain_dual(query, domain, top_k=8):
        capture.append((query, domain))
        return [], []

    retr.retrieve_domain_dual = retrieve_domain_dual
    p._retriever = retr
    return p


async def test_three_way_default_domains_unchanged():
    """常规调用（domains=None）→ 仍是 team/company/industry 三域，行为不变。"""
    capture = []
    p = _platform_with_retriever(capture)
    await p._three_way_retrieve("测试查询")
    assert [d for _, d in capture] == ["team", "company", "industry"]


async def test_three_way_custom_domains():
    """定制模式传入域配额 → 按传入的域检索（含车型域）。"""
    capture = []
    p = _platform_with_retriever(capture)
    await p._three_way_retrieve("货叉不动", domains=[("xqe", 8), ("team", 2)])
    assert [d for _, d in capture] == ["xqe", "team"]


async def test_three_way_custom_domain_exception_tolerated():
    """车型域检索异常 → 降级空列表，不炸主链路。"""
    p = AiDiagnosisPlatform()
    retr = type("R", (), {})()

    async def dual(query, domain, top_k=8):
        if domain == "xqe":
            raise TimeoutError("xqe timeout")
        return [], []

    retr.retrieve_domain_dual = dual
    p._retriever = retr
    results = await p._three_way_retrieve("q", domains=[("xqe", 8), ("team", 2)])
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
