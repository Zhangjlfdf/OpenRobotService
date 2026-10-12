"""归档审核人派审入口：领域 → 名单 → 排除处理人 → 按负荷概率抽签。"""

from __future__ import annotations

import random
from typing import Any, Mapping, Sequence

from ai.agents.AiDiagnosisPlatform.assigner.archive_review.config import (
    ArchiveReviewersConfig,
    load_from_assigner_config,
    parse_archive_reviewers,
)
from ai.agents.AiDiagnosisPlatform.assigner.archive_review.domain import infer_domain
from ai.agents.AiDiagnosisPlatform.assigner.archive_review.load import (
    compute_probabilities,
    pick_by_load_probability,
    snapshot_loads,
)
from ai.agents.AiDiagnosisPlatform.assigner.archive_review.schemas import (
    ArchiveReviewAssignment,
    ArchiveReviewError,
)


def assign_archive_reviewer(
    *,
    title: str = "",
    description: str = "",
    report_content: str = "",
    project_name: str = "",
    product: str = "",
    department: str = "",
    operator_id: str,
    load_counts: Mapping[str, int] | None = None,
    config: ArchiveReviewersConfig | Mapping[str, Any] | None = None,
    domain_hint: str | None = None,
    project_markers: Sequence[tuple[str, str]] | None = None,
    rng: random.Random | None = None,
) -> ArchiveReviewAssignment:
    """按配置选出一名审核人。失败抛 ArchiveReviewError。

    load_counts: reviewer_id → 当前 submit_status=submitted 的在审数量。
    选人按 weight=1/(load+1) 加权抽签，负荷越低概率越高，不是必派给最少的人。
    调用方（后端接入时）负责查库传入；本函数不连库。
    """
    cfg = _resolve_config(config)
    operator = (operator_id or "").strip()

    # 只从「配置里出现过的领域」中判（有名单的优先，否则用配置键）
    allowed = cfg.configured_domains()

    domain = (domain_hint or "").strip() or None
    if domain and domain not in cfg.domains:
        domain = None
    if not domain:
        domain = infer_domain(
            title=title,
            description=description,
            report_content=report_content,
            project_name=project_name,
            product=product,
            department=department,
            allowed_domains=allowed,
            project_markers=project_markers,
        )
    if not domain:
        raise ArchiveReviewError(
            "domain_unknown",
            "无法判定问题领域，请完善工单或报告后再提交",
        )

    candidates = cfg.reviewers_for(domain)
    if not candidates:
        raise ArchiveReviewError(
            "no_reviewers",
            f"问题领域「{domain}」未配置审核人",
        )

    available = [uid for uid in candidates if uid != operator]
    if not available:
        raise ArchiveReviewError(
            "no_available",
            "没有可用审核人（名单为空或仅含处理人本人）",
        )

    loads = snapshot_loads(available, load_counts)
    probs = compute_probabilities(available, loads)
    picked = pick_by_load_probability(available, loads, rng=rng)
    if not picked:
        raise ArchiveReviewError("no_available", "没有可用审核人")

    prob_text = {uid: round(p, 4) for uid, p in probs.items()}
    reason = (
        f"领域={domain}；排除处理人后候选={available}；"
        f"负荷={loads}；抽签概率={prob_text}；选中={picked}"
        f"（权重=1/(在审+1)，按概率抽签）"
    )
    return ArchiveReviewAssignment(
        domain=domain,
        reviewer_id=picked,
        load_snapshot=loads,
        probability_snapshot=probs,
        candidate_ids=list(available),
        reason=reason,
    )


def _resolve_config(
    config: ArchiveReviewersConfig | Mapping[str, Any] | None,
) -> ArchiveReviewersConfig:
    if isinstance(config, ArchiveReviewersConfig):
        return config
    if isinstance(config, Mapping):
        return parse_archive_reviewers(config)
    return load_from_assigner_config(None)
