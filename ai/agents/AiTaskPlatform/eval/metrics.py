"""离线评测的指标计算（纯函数，不碰网络/DB，可直接单测）。

为什么要有这一层：`pass/fail` 单次计数**不能当结论**——同一个输入在
`temperature=0` 下实测也能给出完全相反的判定（见 AiTaskPlatform/issues.md #1–#3）。
所以这里同时算三样东西：

  - **准确率**：与人工标注的期望是否一致；
  - **误杀 / 漏放分开算**：两者后果不同——「误杀」会把本来正确的回答送去重写、
    真的改坏（我们踩过）；「漏放」会放过幻觉。只报一个总准确率会把这两件事糊在一起；
  - **多轮一致率**：同一用例跑 K 轮是否同判。**翻转的用例才是要去看的清单**，
    因为它说明这条判据在这类输入上不可靠，而不是模型「偶尔手滑」。

`None` 表示调用失败或解析失败，**单独计数**：既不算通过也不算不通过，避免把
「判官没跑起来」美化成判定结果。
"""

from __future__ import annotations

from typing import Iterable, Optional

# 判定取值：True=通过 / False=不通过 / None=未解析
Verdict = Optional[bool]


def _round_verdicts(case: dict, round_idx: Optional[int] = None) -> list:
    verdicts = list(case.get("verdicts") or [])
    if round_idx is None:
        return verdicts
    return verdicts[round_idx : round_idx + 1]


def _pairs(records: Iterable[dict], round_idx: Optional[int] = None) -> list:
    """(期望, 实际判定) 列表；跳过未解析的那些（它们单独统计）。"""
    out = []
    for case in records:
        for got in _round_verdicts(case, round_idx):
            if got is None:
                continue
            out.append((bool(case.get("expected")), got))
    return out


def count_verdicts(records: Iterable[dict]) -> dict:
    """通过 / 不通过 / 未解析各多少次（跨全部轮次）。"""
    total = {"pass": 0, "fail": 0, "unparsed": 0}
    for case in records:
        for got in _round_verdicts(case):
            if got is None:
                total["unparsed"] += 1
            elif got:
                total["pass"] += 1
            else:
                total["fail"] += 1
    return total


def confusion(records: Iterable[dict], round_idx: Optional[int] = None) -> dict:
    """以「期望通过」为正类：误杀（应通过→判不通过）与漏放（应不通过→判通过）分开算。"""
    hit = false_reject = false_accept = 0
    for expected, got in _pairs(records, round_idx):
        if got == expected:
            hit += 1
        elif expected:
            false_reject += 1  # 误杀：会把正确回答送去重写
        else:
            false_accept += 1  # 漏放：放过幻觉/偏题
    return {
        "hit": hit,
        "false_reject": false_reject,
        "false_accept": false_accept,
        "comparable": hit + false_reject + false_accept,
    }


def accuracy(records: Iterable[dict], round_idx: Optional[int] = None) -> dict:
    """准确率。分母是「解析成功的判定次数」，未解析的不计入分母（但报告里单列）。"""
    c = confusion(records, round_idx)
    rate = (c["hit"] / c["comparable"]) if c["comparable"] else 0.0
    return {**c, "rate": rate}


def stability(records: Iterable[dict]) -> dict:
    """多轮一致率 + 翻转用例清单（要看的就这份）。"""
    consistent, flipped, unparsed_only = 0, [], []
    for case in records:
        seen = {v for v in _round_verdicts(case) if v is not None}
        if not seen:
            unparsed_only.append(case.get("case_id"))
        elif len(seen) == 1:
            consistent += 1
        else:
            flipped.append(case.get("case_id"))
    scored = consistent + len(flipped)
    return {
        "consistent": consistent,
        "scored": scored,
        "rate": (consistent / scored) if scored else 0.0,
        "flipped": flipped,
        "unparsed_only": unparsed_only,
    }


def summarize(records: list) -> dict:
    """整份报告要用到的全部指标。"""
    cases = [dict(c) for c in records]
    return {
        "cases": len(cases),
        "rounds": max((len(c.get("verdicts") or []) for c in cases), default=0),
        "counts": count_verdicts(cases),
        "overall": accuracy(cases),
        "first_round": accuracy(cases, round_idx=0),
        "stability": stability(cases),
    }


def format_case_line(case: dict) -> str:
    """一行的用例明细：期望 → 各轮判定。"""
    marks = ["—" if got is None else ("通过" if got else "不通过")
             for got in (case.get("verdicts") or [])]
    expected = "通过" if case.get("expected") else "不通过"
    seen = {v for v in (case.get("verdicts") or []) if v is not None}
    if len(seen) > 1:
        flag = "  ⚠️ 翻转"
    elif seen and seen != {bool(case.get("expected"))}:
        flag = "  ❌ 判错"
    else:
        flag = ""
    return f"  {str(case.get('case_id')):<44} 期望={expected:<4} 各轮=[{', '.join(marks)}]{flag}"


def format_report(summary: dict, records: list) -> str:
    """控制台报告。刻意把「误杀/漏放」和「翻转清单」放在显眼处，不只报一个准确率。"""
    lines = []
    ov, st = summary["overall"], summary["stability"]
    total = summary["counts"]["pass"] + summary["counts"]["fail"] + summary["counts"]["unparsed"]
    lines.append(f"用例 {summary['cases']} 条 × {summary['rounds']} 轮 = {total} 次判定")
    lines.append("")
    lines.append(f"准确率（全部轮次）  {ov['hit']}/{ov['comparable']} = {ov['rate']:.1%}")
    fr = summary["first_round"]
    lines.append(f"  其中首轮          {fr['hit']}/{fr['comparable']} = {fr['rate']:.1%}")
    lines.append(f"  ❌ 误杀（正确被判不通过、会触发重写） {ov['false_reject']}")
    lines.append(f"  ⚠️ 漏放（幻觉/偏题被判通过）          {ov['false_accept']}")
    lines.append(f"  未解析（调用或解析失败）              {summary['counts']['unparsed']}")
    lines.append("")
    lines.append(f"多轮一致率          {st['consistent']}/{st['scored']} = {st['rate']:.1%}")
    if st["flipped"]:
        lines.append(f"  ⚠️ 翻转用例（判据在这类输入上不可靠）：{'、'.join(st['flipped'])}")
    if st["unparsed_only"]:
        lines.append(f"  全程未解析的用例：{'、'.join(st['unparsed_only'])}")
    lines.append("")
    lines.append("用例明细：")
    lines.extend(format_case_line(c) for c in records)
    return "\n".join(lines)
