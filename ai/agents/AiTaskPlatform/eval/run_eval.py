#!/usr/bin/env python3
"""AiTaskPlatform 离线评测：跑用例 → 算指标 → 出报告。

设计口径（照 AiDiagnosisPlatform/assigner/eval 的做法）：
  - **用例集随仓提交**（`datasets/*.json`），不连库、不需要服务在跑；只要求 LLM 可用；
  - **指标只看两件事**：与人工标注期望的一致性、同一用例多轮是否同判。
    单跑一次的 pass/fail 不构成结论——`temperature=0` 也可以完全相反（见 issues.md #1–#3）；
  - **评测量到的就是生产在跑的那条路径**：走 `Evaluator.judge()`，与
    `evaluate_and_rewrite` 同一个入口，判据改了评测口径自动跟着变。

用法:
    python -m ai.agents.AiTaskPlatform.eval.run_eval --dataset evaluator_verdicts --rounds 3
    python -m ai.agents.AiTaskPlatform.eval.run_eval --dataset evaluator_verdicts --only discuss-contradicts --rounds 5
    python -m ai.agents.AiTaskPlatform.eval.run_eval --list
    python -m ai.agents.AiTaskPlatform.eval.run_eval --rounds 3 --json out/eval.json
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


# ── 用例执行器：按数据集 kind 分派 ──
async def _run_evaluator_case(llm, case: dict) -> Optional[bool]:
    """自评用例：返回判定（True/False），调用或解析失败返回 None。"""
    from ai.agents.AiTaskPlatform.capabilities import Evaluator

    data = await Evaluator.judge(
        llm,
        draft=case.get("draft", ""),
        evidence=case.get("evidence", ""),
        context=case.get("context", ""),
        mode=case.get("mode", "diagnose"),
    )
    return None if data is None else bool(data.get("pass"))


# 新用例类型 = 加一个执行器 + 这里登记一行（数据集里用 "kind" 指过来）
RUNNERS: dict[str, Callable[..., Coroutine]] = {"evaluator": _run_evaluator_case}


def load_dataset(name: str) -> dict:
    path = _DATASETS / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"数据集不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not data.get("cases"):
        raise ValueError(f"数据集格式不对（需要 {{\"kind\": ..., \"cases\": [...]}}）: {path}")
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
    records = [{"case_id": c.get("case_id"), "expected": bool(c.get("expected")),
                "mode": c.get("mode", ""), "why": c.get("why", ""), "verdicts": []}
               for c in cases]

    for r in range(rounds):
        for case, rec in zip(cases, records):
            got = await runner(llm, case)
            rec["verdicts"].append(got)
            mark = "—" if got is None else ("通过" if got else "不通过")
            print(f"  [第 {r + 1}/{rounds} 轮] {rec['case_id']}: {mark}", flush=True)
    return records


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="AiTaskPlatform 离线评测")
    ap.add_argument("--dataset", default=DEFAULT_DATASET, help="datasets/<名字>.json")
    ap.add_argument("--rounds", type=int, default=3, help="每个用例跑几轮（默认 3）")
    ap.add_argument("--only", default="", help="只跑这些用例（逗号分隔，支持前缀）")
    ap.add_argument("--json", dest="json_out", default="", help="把结果写到这个文件")
    ap.add_argument("--list", action="store_true", help="只列用例，不跑")
    ap.add_argument("--fail-under-consistency", type=float, default=None,
                    help="多轮一致率低于它就返回 1（用于 CI 门槛）")
    ap.add_argument("--fail-under-accuracy", type=float, default=None,
                    help="准确率低于它就返回 1")
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
            print(f"{c.get('case_id'):<44} 期望={'通过' if c.get('expected') else '不通过'}  {c.get('why', '')}")
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
    if args.fail_under_accuracy is not None and summary["overall"]["rate"] < args.fail_under_accuracy:
        print(f"✗ 准确率 {summary['overall']['rate']:.1%} < 门槛 {args.fail_under_accuracy:.1%}", file=sys.stderr)
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
