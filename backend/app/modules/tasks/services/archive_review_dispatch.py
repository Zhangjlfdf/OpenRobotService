"""归档审核人派审桥接：后端 submit 时调用 assigner.archive_review。

不走工程师派单 DispatchFlow / task_dispatch_log。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import ArchiveReport, Task

logger = logging.getLogger(__name__)


def _ensure_repo_on_path() -> None:
    # .../backend/app/modules/tasks/services/this.py → repo root = parents[5]
    repo = Path(__file__).resolve().parents[5]
    root = str(repo)
    if root not in sys.path:
        sys.path.insert(0, root)


def _import_assigner():
    _ensure_repo_on_path()
    from ai.agents.AiDiagnosisPlatform.assigner.archive_review import (
        ArchiveReviewAssignment,
        ArchiveReviewError,
        assign_archive_reviewer,
    )
    from ai.agents.AiDiagnosisPlatform.assigner.settings import (
        effective_archive_reviewers,
    )

    return (
        assign_archive_reviewer,
        ArchiveReviewError,
        ArchiveReviewAssignment,
        effective_archive_reviewers,
    )


async def count_submitted_loads(
    db: AsyncSession,
    reviewer_ids: list[str] | None = None,
) -> dict[str, int]:
    """统计 submit_status=submitted 的在审数量。"""
    stmt = (
        select(ArchiveReport.reviewer, func.count())
        .where(ArchiveReport.submit_status == "submitted")
        .where(ArchiveReport.reviewer.is_not(None))
        .where(ArchiveReport.reviewer != "")
        .group_by(ArchiveReport.reviewer)
    )
    if reviewer_ids:
        stmt = stmt.where(ArchiveReport.reviewer.in_(list(reviewer_ids)))
    result = await db.execute(stmt)
    out: dict[str, int] = {}
    for reviewer, cnt in result.all():
        if reviewer:
            out[str(reviewer)] = int(cnt or 0)
    return out


async def dispatch_archive_reviewer(
    db: AsyncSession,
    *,
    task: Task,
    report: ArchiveReport,
    operator_id: str,
    content_override: Optional[str] = None,
) -> Any:
    """判定领域并按负荷概率选出审核人。失败抛 ArchiveReviewError。"""
    (
        assign_archive_reviewer,
        ArchiveReviewError,
        _Assignment,
        effective_archive_reviewers,
    ) = _import_assigner()

    try:
        cfg = effective_archive_reviewers()
    except Exception as exc:  # noqa: BLE001 — 配置异常视为服务失败
        logger.exception("加载 archive_reviewers 配置失败")
        raise ArchiveReviewError("service_error", "归档派审服务异常，请稍后重试") from exc

    # 先取全量在审负荷；抽签只使用候选子集
    try:
        loads = await count_submitted_loads(db)
    except Exception as exc:  # noqa: BLE001
        logger.exception("统计归档在审负荷失败")
        raise ArchiveReviewError("service_error", "归档派审服务异常，请稍后重试") from exc

    report_body = content_override if content_override is not None else (report.content or "")
    # metadata / AI 摘要里常含领域线索（产品、故障摘要），拼进判定文本
    meta_bits: list[str] = []
    meta = getattr(task, "metadata_info", None) or {}
    if isinstance(meta, dict):
        for key in ("product", "robot_type", "ai_summary", "resolution_summary"):
            val = meta.get(key)
            if isinstance(val, str) and val.strip():
                meta_bits.append(val.strip())
        diagnosis = meta.get("diagnosis")
        if isinstance(diagnosis, dict):
            for key in ("problem_summary",):
                val = diagnosis.get(key)
                if isinstance(val, str) and val.strip():
                    meta_bits.append(val.strip())
        product_hint = str(meta.get("product") or "").strip()
    else:
        product_hint = ""

    enriched_report = "\n".join([report_body, *meta_bits]).strip()
    # 工单相关人优先脱敏（不审自己的单）；名单只剩这些人时由 assign 侧回落仍可派
    exclude_ids = [
        x for x in (
            operator_id,
            getattr(task, "assigned_to", None),
            getattr(task, "created_by", None),
        )
        if (x or "").strip()
    ]
    try:
        return assign_archive_reviewer(
            title=task.title or "",
            description=task.description or "",
            report_content=enriched_report,
            project_name=task.project_name or "",
            product=product_hint,
            department=(getattr(task, "team", None) or "") or "",
            operator_id=operator_id,
            exclude_ids=exclude_ids,
            load_counts=loads,
            config=cfg,
        )
    except ArchiveReviewError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("归档派审选人异常 task_id=%s report_id=%s", task.id, report.id)
        raise ArchiveReviewError("service_error", "归档派审服务异常，请稍后重试") from exc


def assignment_log_detail(assignment: Any) -> dict[str, Any]:
    """写入操作日志 detail 的结构化快照。"""
    probs = getattr(assignment, "probability_snapshot", None) or {}
    return {
        "domain": getattr(assignment, "domain", None),
        "reviewer": getattr(assignment, "reviewer_id", None),
        "candidates": list(getattr(assignment, "candidate_ids", None) or []),
        "load_snapshot": dict(getattr(assignment, "load_snapshot", None) or {}),
        "probability_snapshot": {k: round(float(v), 4) for k, v in probs.items()},
        "reason": getattr(assignment, "reason", None),
        "source": "archive_review_dispatch",
    }
