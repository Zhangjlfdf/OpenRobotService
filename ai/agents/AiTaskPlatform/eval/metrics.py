"""离线评测的指标（纯函数，不碰网络/DB，可直接单测）。

**这里刻意不评「判得对不对」。** 讨论场景不该有固定的通过/不通过标准——标准取决于
用户问的是什么，同一份回答在不同提问下对错不同（见 AiTaskPlatform/issues.md #1–#3
与 eval/README.md 的实测）。固定的「期望通过/不通过」标签是错的工具，已删除。

只留两件与「具体问了什么」无关、能客观量的事：

  1. **判定稳定性**：同一用例多轮是否同判（`stability`）；
  2. **issues 落点**：判官挑的每条刺，指的东西在不在本次材料里（`verify_report`）。
     这一步**由 prompt 复核**（`Evaluator.verify_issues`），不做字符串匹配——评审意见是
     中文散文，机械比对测出来的是「用词像不像」，不是「有没有落点」（实测过：换词复述
     材料内容会被算成没落点，常用词又会凑出假重叠）。

另外只统计**分布**（通过/不通过/未解析各几次）作为信息，不做评判。`None` 一律单独计数：
判定为 None = 调用或解析失败；某轮复核为 None = 复核没跑成（不能当成「落点没问题」）。
"""

from __future__ import annotations

from typing import Iterable, Optional


def _round_verdicts(case: dict, round_idx: Optional[int] = None) -> list:
    verdicts = list(case.get("verdicts") or [])
    if round_idx is None:
        return verdicts
    return verdicts[round_idx : round_idx + 1]


def count_verdicts(records: Iterable[dict]) -> dict:
    """通过 / 不通过 / 未解析各多少次（跨全部轮次）。只是分布，不代表对错。"""
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


def stability(records: Iterable[dict]) -> dict:
    """多轮一致率 + 翻转用例清单（稳定性的问题都在这份清单上）。"""
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


def verify_report(records: Iterable[dict]) -> dict:
    """汇总 prompt 复核结果。

    入参 `case["verifications"]` 是每轮的复核结果：
      - `None`  = 这一轮有评审意见，但复核没跑成（调用/解析失败）→ 单列，**不能当成没问题**
      - `[]`    = 这一轮没有评审意见（判定通过），无需复核
      - `[{issue, grounded, why}, ...]` = 逐条复核结果

    `suspicious` 是 grounded=false 的明细：复核员认为这条意见指的东西本次材料里没有，
    多半在套别的场景模板。**这是可疑清单，不是判决**——复核本身也是 LLM。
    """
    grounded = not_grounded = 0
    failed_rounds = 0
    suspicious = []
    for case in records:
        for r, checks in enumerate(case.get("verifications") or []):
            if checks is None:
                failed_rounds += 1
                continue
            for item in checks:
                if not isinstance(item, dict):
                    continue
                if item.get("grounded"):
                    grounded += 1
                else:
                    not_grounded += 1
                    suspicious.append({
                        "case_id": case.get("case_id"),
                        "round": r + 1,
                        "issue": item.get("issue", ""),
                        "why": item.get("why", ""),
                    })
    checkable = grounded + not_grounded
    return {
        "grounded": grounded,
        "not_grounded": not_grounded,
        "failed_rounds": failed_rounds,
        "rate": (grounded / checkable) if checkable else 0.0,
        "suspicious": suspicious,
    }


def summarize(records: list) -> dict:
    """整份报告要用到的全部指标。"""
    cases = [dict(c) for c in records]
    return {
        "cases": len(cases),
        "rounds": max((len(c.get("verdicts") or []) for c in cases), default=0),
        "counts": count_verdicts(cases),
        "stability": stability(cases),
        "verify": verify_report(cases),
    }


def format_case_line(case: dict) -> str:
    """一行的用例明细：各轮判定 + 挑了几条问题。"""
    marks = ["—" if got is None else ("通过" if got else "不通过")
             for got in (case.get("verdicts") or [])]
    counts = [len(x or []) for x in (case.get("issues") or [])]
    seen = {v for v in (case.get("verdicts") or []) if v is not None}
    flag = "  ⚠️ 翻转" if len(seen) > 1 else ""
    return (f"  {str(case.get('case_id')):<44} 各轮=[{', '.join(marks)}]"
            f"  挑出问题数=[{', '.join(str(c) for c in counts)}]{flag}")


def format_report(summary: dict, records: list) -> str:
    """控制台报告。刻意**不给**「准确率」，只给稳定性 + 复核落点 + 分布。"""
    st, vf, ct = summary["stability"], summary["verify"], summary["counts"]
    lines = []
    total = ct["pass"] + ct["fail"] + ct["unparsed"]
    lines.append(f"用例 {summary['cases']} 条 × {summary['rounds']} 轮 = {total} 次判定")
    lines.append("")
    lines.append("判定分布（只是分布，不是对错）  通过 {} / 不通过 {} / 未解析 {}".format(
        ct["pass"], ct["fail"], ct["unparsed"]))
    lines.append(f"多轮一致率          {st['consistent']}/{st['scored']} = {st['rate']:.1%}")
    if st["flipped"]:
        lines.append(f"  ⚠️ 翻转用例（同一输入判得不一样，要去看）：{'、'.join(st['flipped'])}")
    if st["unparsed_only"]:
        lines.append(f"  全程未解析的用例：{'、'.join(st['unparsed_only'])}")
    lines.append("")
    checkable = vf["grounded"] + vf["not_grounded"]
    if checkable:
        rate = f"{vf['grounded']}/{checkable} = {vf['rate']:.1%}"
    else:
        rate = "无可复核的条目"
    lines.append(f"评审意见落点（prompt 复核）  {rate}")
    if vf["failed_rounds"]:
        lines.append(f"  ⚠️ 有 {vf['failed_rounds']} 轮复核没跑成（调用/解析失败）——不算「没问题」")
    if vf["suspicious"]:
        lines.append("  ❓ 复核认为「指的东西材料里没有」的意见（可疑：可能在套别的场景模板）——")
        for item in vf["suspicious"]:
            lines.append(f"     [{item['case_id']} 第{item['round']}轮] {item['issue']}")
            if item["why"]:
                lines.append(f"        复核说明：{item['why']}")
    lines.append("")
    lines.append("用例明细：")
    lines.extend(format_case_line(c) for c in records)
    return "\n".join(lines)
