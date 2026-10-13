#!/usr/bin/env python3
"""AiTaskPlatform 离线评测：跑用例 → 观测判官行为 → 出报告。

**这套评测量什么、不量什么**（口径由 2026-10-12 的实测定下来，别再退回旧口径）：

  - 量：①**判定稳定性**（同一用例多轮是否同判）
        ②**评审意见的落点**——判官挑的每条刺，指的东西在不在本次材料里。
          这一步**用 prompt 复核**（`Evaluator.verify_issues`），不做字符串匹配：
          评审意见是中文散文，机械比对量出来的是「用词像不像」，不是「有没有落点」
          （实测：换词复述材料内容会被算成没落点，常用词又会凑出假重叠）。
  - **不量**：「判得对不对」。讨论场景没有固定标准——标准取决于用户问的是什么，
    同一份回答在不同提问下对错不同，固定的「期望通过/不通过」标签是错的工具。
    实测证据：把判据换回诊断口径、甚至抽掉 `_JUDGE_RULES`，手写的「陷阱」用例照样全通过。

跑法（要真实 LLM，读 ai/.env；不需要数据库、不需要 AI 服务在跑）：

    python -m ai.agents.AiTaskPlatform.eval.run_eval --dataset evaluator_verdicts --rounds 3
    python -m ai.agents.AiTaskPlatform.eval.run_eval --only discuss-contradicts --rounds 5
    python -m ai.agents.AiTaskPlatform.eval.run_eval --list
    python -m ai.agents.AiTaskPlatform.eval.run_eval --rounds 3 --json /tmp/eval.json
    python -m ai.agents.AiTaskPlatform.eval.run_eval --rounds 3 --fail-under-consistency 0.9

退出码：0 正常；1 未达 --fail-under-* 门槛；2 用法/数据集错误。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Callable, Coroutine, Optional

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_HERE = Path(__file__).resolve().parent
_DATASETS = _HERE / "datasets"
_PROJECT_ROOT = _HERE.parents[3]  # eval → AiTaskPlatform → agents → ai → 仓库根
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_PROJECT_ROOT / "ai" / ".env")

from ai.agents.AiTaskPlatform.eval import metrics  # noqa: E402

DEFAULT_DATASET = "evaluator_verdicts"


# ── 用例执行器：按数据集 kind 分派，返回「这一轮的观测量」──
async def _run_evaluator_case(llm, case: dict) -> dict:
    """自评用例：跑判官；有问题时再用 prompt 复核这些问题的落点。

    Returns:
        {"verdict": bool|None, "issues": [str], "verifications": list|None}
        verifications：[] = 这轮没有意见可复核；None = 复核没跑成（不算「没问题」）。
    """
    from ai.agents.AiTaskPlatform.capabilities import Evaluator

    kw = {
        "draft": case.get("draft", ""),
        "evidence": case.get("evidence", ""),
        "context": case.get("context", ""),
    }
    data = await Evaluator.judge(llm, mode=case.get("mode", "diagnose"), **kw)
    if data is None:
        return {"verdict": None, "issues": [], "verifications": None}

    issues = [str(x) for x in (data.get("issues") or [])]
    verifications: Optional[list] = []
    if issues:
        checks = await Evaluator.verify_issues(llm, issues, **kw)
        if checks is None:
            verifications = None
        else:
            verifications = [
                {**check, "issue": _issue_at(issues, check.get("index"))}
                for check in checks
            ]
    return {"verdict": bool(data.get("pass")), "issues": issues, "verifications": verifications}


def _issue_at(issues: list, index) -> str:
    """复核返回的 index 是清单序号（1 起）；越界/非数字就留空，不猜。"""
    if isinstance(index, int) and 1 <= index <= len(issues):
        return issues[index - 1]
    return ""


# 新用例类型 = 加一个执行器 + 这里登记一行（数据集里用 "kind" 指过来）
RUNNERS: dict[str, Callable[..., Coroutine]] = {"evaluator": _run_evaluator_case}


# 本地生成、不入库的数据集（见 .gitignore）：干净克隆上没有，给条生成命令
_GENERATED = {
    "discuss_real_cases": "python -m ai.agents.AiTaskPlatform.eval.build_dataset_from_db --limit 20",
}


def load_dataset(name: str) -> dict:
    path = _DATASETS / f"{name}.json"
    if not path.exists():
        hint = (f"\n  → 这个数据集不入库（含真实人名），需本地生成：{_GENERATED[name]}"
                if name in _GENERATED else "")
        raise FileNotFoundError(f"数据集不存在: {path}{hint}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not data.get("cases"):
        raise ValueError(f'数据集格式不对（需要 {{"kind": ..., "cases": [...]}}）: {path}')
    return data


def pick_cases(cases: list, only: str = "") -> list:
    """--only a,b：id 精确匹配或前缀匹配（方便只跑某一类）。"""
    keys = [k.strip() for k in (only or "").split(",") if k.strip()]
    if not keys:
        return cases
    return [c for c in cases
            if any(str(c.get("case_id")) == k or str(c.get("case_id")).startswith(k) for k in keys)]


async def run(dataset: dict, cases: list, rounds: int) -> list:
    kind = dataset.get("kind", "evaluator")
    runner = RUNNERS.get(kind)
    if runner is None:
        raise ValueError(f"未知数据集类型: {kind}（可选：{', '.join(RUNNERS)}）")

    from ai.core.llm import get_llm_client

    llm = await get_llm_client()
    records = [{
        "case_id": c.get("case_id"), "mode": c.get("mode", ""), "why": c.get("why", ""),
        "context": c.get("context", ""), "evidence": c.get("evidence", ""),
        "draft": c.get("draft", ""), "verdicts": [], "issues": [], "verifications": [],
    } for c in cases]

    for r in range(rounds):
        for case, rec in zip(cases, records):
            observed = await runner(llm, case)
            verdict = observed.get("verdict")
            issues = list(observed.get("issues") or [])
            rec["verdicts"].append(verdict)
            rec["issues"].append(issues)
            rec["verifications"].append(observed.get("verifications"))
            mark = "—" if verdict is None else ("通过" if verdict else f"不通过({len(issues)}条)")
            print(f"  [第 {r + 1}/{rounds} 轮] {rec['case_id']}: {mark}{_verify_note(observed)}", flush=True)
    return records


def _verify_note(observed: dict) -> str:
    """进度行里的复核结果：好/可疑/复核失败，一眼能看出这轮到底核没核。"""
    checks = observed.get("verifications")
    if checks is None and observed.get("issues"):
        return "  复核=失败"
    if not checks:
        return ""
    bad = sum(1 for c in checks if not c.get("grounded"))
    return f"  复核={len(checks) - bad}落点/{bad}可疑"


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="AiTaskPlatform 离线评测（稳定性 + prompt 复核落点）")
    ap.add_argument("--dataset", default=DEFAULT_DATASET, help="datasets/<名字>.json")
    ap.add_argument("--rounds", type=int, default=3, help="每个用例跑几轮（默认 3）")
    ap.add_argument("--only", default="", help="只跑这些用例（逗号分隔，支持前缀）")
    ap.add_argument("--json", dest="json_out", default="", help="把结果写到这个文件")
    ap.add_argument("--list", action="store_true", help="只列用例，不跑")
    ap.add_argument("--fail-under-consistency", type=float, default=None,
                    help="多轮一致率低于它就返回 1（用于 CI 门槛）")
    ap.add_argument("--fail-under-grounded-rate", type=float, default=None,
                    help="评审意见落点率低于它就返回 1")
    args = ap.parse_args(argv)

    try:
        dataset = load_dataset(args.dataset)
    except (FileNotFoundError, ValueError) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2

    cases = pick_cases(dataset["cases"], args.only)
    if not cases:
        print("✗ 没有匹配的用例", file=sys.stderr)
        return 2

    if args.list:
        for c in cases:
            print(f"{str(c.get('case_id')):<44} mode={c.get('mode', ''):<9} {c.get('why', '')}")
        return 0

    if args.rounds < 1:
        print("✗ --rounds 至少为 1", file=sys.stderr)
        return 2

    print(f"数据集 {args.dataset}（kind={dataset.get('kind')}）：{len(cases)} 条用例 × {args.rounds} 轮")
    records = asyncio.run(run(dataset, cases, args.rounds))
    summary = metrics.summarize(records)

    print()
    print(metrics.format_report(summary, records))

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"dataset": args.dataset, "summary": summary, "records": records},
                      f, ensure_ascii=False, indent=2)
        print(f"\n明细已写入 {out}")

    failed = False
    if args.fail_under_consistency is not None and summary["stability"]["rate"] < args.fail_under_consistency:
        print(f"✗ 一致率 {summary['stability']['rate']:.1%} < 门槛 {args.fail_under_consistency:.1%}", file=sys.stderr)
        failed = True
    if args.fail_under_grounded_rate is not None and summary["verify"]["rate"] < args.fail_under_grounded_rate:
        print(f"✗ 落点率 {summary['verify']['rate']:.1%} < 门槛 {args.fail_under_grounded_rate:.1%}", file=sys.stderr)
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
