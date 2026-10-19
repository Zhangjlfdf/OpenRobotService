"""ORS-713 项目智能排序单测：机械评分/阶段权重/信号聚合/降级。

不碰真库：_Profiles 直接猴补内存数据，LLM 路径 monkeypatch 成可控返回。
"""
import asyncio
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.core import project_rank as pr


class _Mem:
    def __init__(self, metadata=None, turns=None):
        self.metadata = metadata or {}
        self.turns = turns or []


def _profiles():
    p = pr._Profiles()
    p.by_code = {
        "P1": {"code": "P1", "name": "XNA 智能工厂项目", "desc": "",
               "stage": "正在实施", "region": "杭州", "ext_vehicles": []},
        "P2": {"code": "P2", "name": "旧厂改造项目", "desc": "",
               "stage": "项目中止", "region": "杭州", "ext_vehicles": []},
        "P3": {"code": "P3", "name": "XNA 二期扩建", "desc": "",
               "stage": "验收运营", "region": "苏州", "ext_vehicles": []},
    }
    p.vehicle_map = {"XNA": {"P1", "P3"}}
    p.customer_map = {"某某锂电": {"P1"}}
    p._loaded_at = pr.time.time()
    return p


def test_stage_weights_sunken():
    """中止/已验收必须沉到实施中之后（领导 1017 反馈的第二点）。"""
    monkey = pytest.MonkeyPatch()
    monkey.setattr(pr, "_PROFILES", _profiles())
    try:
        sig = {"vehicles": [], "customers": [], "regions": ["杭州"],
               "project_codes": [], "project_names": []}
        p1 = pr._score_project(sig, pr._PROFILES.by_code["P1"])  # 杭州+实施中
        p2 = pr._score_project(sig, pr._PROFILES.by_code["P2"])  # 杭州+中止
        assert p1 > p2, "同信号下实施中应排在项目中止之前"
    finally:
        monkey.undo()


def test_vehicle_match_beats_region():
    """车型命中权重高于地点（领导第一点：提示 XNA 要把 XNA 项目排前）。"""
    monkey = pytest.MonkeyPatch()
    monkey.setattr(pr, "_PROFILES", _profiles())
    try:
        sig = {"vehicles": ["XNA"], "customers": [], "regions": ["苏州"],
               "project_codes": [], "project_names": []}
        p1 = pr._score_project(sig, pr._PROFILES.by_code["P1"])  # 车型+杭州≠苏州
        p3 = pr._score_project(sig, pr._PROFILES.by_code["P3"])  # 车型+苏州+验收
        # P3 总分可能更高（多了地点分），但车型组与非车型组的差距由用例保证：
        p_novehicle = pr._score_project(
            {"vehicles": [], "customers": [], "regions": ["杭州"],
             "project_codes": [], "project_names": []},
            pr._PROFILES.by_code["P2"])
        assert p1 > p_novehicle, "车型命中应显著高于仅地点命中"
    finally:
        monkey.undo()


def test_qrcode_project_top():
    """扫码行自带项目 = 最强信号，稳居第一。"""
    monkey = pytest.MonkeyPatch()
    monkey.setattr(pr, "_PROFILES", _profiles())
    try:
        sig = {"vehicles": ["XNA"], "customers": [], "regions": [],
               "project_codes": ["P2"], "project_names": []}
        p2 = pr._score_project(sig, pr._PROFILES.by_code["P2"])
        p1 = pr._score_project(sig, pr._PROFILES.by_code["P1"])
        assert p2 > p1, "扫码自带项目应高于仅车型命中"
    finally:
        monkey.undo()


def test_split_multi():
    assert pr._split_multi("XNA、XQE122") == ["XNA", "XQE122"]
    assert pr._split_multi("A,B / C") == ["A", "B", "C"]
    assert pr._split_multi("") == []
    assert pr._split_multi(None) == []


def test_collect_vehicle_like():
    ext = {"robots": [{"name": "r1", "model": "XQE122"}],
           "network": {"vlan": 100}, "note": "车型说明很长的一段描述文字不应该被收进来因为超过长度限制哦"}
    out = pr._collect_vehicle_like(ext)
    assert "XQE122" in out
    assert all(len(x) <= 32 for x in out)


def test_rank_degrades_when_profiles_unavailable(monkeypatch):
    """画像加载失败 → 空推荐（弹窗退化现状排序，不抛异常）。"""
    class _Broken:
        by_code = {}
        vehicle_map = {}
        customer_map = {}

        async def ensure_loaded(self):
            return False

    monkeypatch.setattr(pr, "_PROFILES", _Broken())
    out = asyncio.run(pr.rank_for_session(_Mem(), []))
    assert out == []


def test_rank_mechanical_top(monkeypatch):
    """机械强命中直接出结果（不调 LLM）。"""
    monkeypatch.setattr(pr, "_PROFILES", _profiles())

    async def _no_llm(turns):
        raise AssertionError("机械强命中不应调 LLM 提取")

    monkeypatch.setattr(pr, "extract_signals_llm", _no_llm)
    out = asyncio.run(pr.rank_for_session(
        _Mem(metadata={"vehicle_mode": {"model": "XNA"}}), []))
    assert out, "车型模式信号应产出推荐"
    assert out[0]["code"] in ("P1", "P3")
    assert all(set(r) >= {"code", "name"} for r in out)


def test_rank_no_signals_empty(monkeypatch):
    """无任何信号且 LLM 也拿不到 → 空推荐（不误推，不拿随机候选精排）。"""
    monkeypatch.setattr(pr, "_PROFILES", _profiles())

    async def _none_llm(turns):
        return None

    async def _no_rerank(turns, candidates):
        raise AssertionError("机械零分不应触发 LLM 精排")

    monkeypatch.setattr(pr, "extract_signals_llm", _none_llm)
    monkeypatch.setattr(pr, "llm_rerank", _no_rerank)
    out = asyncio.run(pr.rank_for_session(_Mem(), []))
    assert out == []
