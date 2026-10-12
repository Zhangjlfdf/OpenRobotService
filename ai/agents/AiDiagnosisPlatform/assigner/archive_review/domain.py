"""问题领域判定（首版：规则，不调 LLM）。

优先项目/产品映射，再按正文关键词打分；只从「允许的领域名单」中选。
后续可替换为 dual_tree 的 LLM 判定，接口保持返回单一主领域。
"""

from __future__ import annotations

from typing import Iterable, Optional

from ai.agents.AiDiagnosisPlatform.assigner.dual_tree.catalog import (
    DEPT_DOMAIN,
    DOMAINS,
    PRODUCT_DOMAIN,
)

# 产品名别名 → 标准产品（与 product_routing / PRODUCT_DOMAIN 对齐）
_PRODUCT_ALIASES = {
    "摇人吧服务号": "摇人吧服务号",
    "服务号": "摇人吧服务号",
    "调度USP": "调度USP",
    "USP": "调度USP",
    "车端软件": "车端软件",
    "车端硬件": "车端硬件",
    "车体硬件": "车端硬件",
}

# 领域关键词：命中加分；仅作规则兜底
_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "调度系统": (
        "调度", "USP", "任务下发", "路径规划", "锁区", "地图编辑", "阻塞", "路网",
        # 外设 / 电梯 / 信号类现场单（常见归调度侧）
        "电梯", "外设", "进车信号", "出车信号", "呼叫电梯", "楼层", "PLC",
        "外设平台", "外设任务", "外设管理",
    ),
    "车体硬件": (
        "轮子", "电池", "电机", "货叉", "车体", "碰撞", "撞门", "机械", "底盘", "充不进电",
    ),
    "车端软件": (
        "定位", "SLAM", "雷达", "传感器", "控制器", "重定位", "车载", "网关",
    ),
    "摇人吧": (
        "摇人吧", "服务号", "小程序", "后台管理", "派单页面",
    ),
    "上游对接": (
        "上位系统", "WMS", "MES", "对接", "接口联调",
    ),
    "实施工具": (
        "实施工具", "部署工具", "现场实施", "调试工具",
    ),
    "售前": (
        "售前", "选型", "报价", "方案咨询",
    ),
}


def _norm(text: str) -> str:
    return (text or "").replace(" ", "").replace("\u3000", "")


def product_to_domain(product: str) -> str:
    p = (product or "").strip()
    if not p:
        return ""
    p = _PRODUCT_ALIASES.get(p, p)
    return PRODUCT_DOMAIN.get(p, "")


def infer_product_from_project(project_name: str, project_markers: Iterable[tuple[str, str]] | None = None) -> str:
    """项目名命中 marker → 产品名。markers 为 (marker, product) 列表。"""
    norm = _norm(project_name)
    if not norm:
        return ""
    if project_markers:
        for marker, product in project_markers:
            m = _norm(marker)
            if m and m in norm:
                return (product or "").strip()
    # 内置常见 marker
    builtins = (
        ("摇人吧服务号", "摇人吧服务号"),
        ("摇人吧", "摇人吧服务号"),
    )
    for marker, product in builtins:
        if _norm(marker) in norm:
            return product
    return ""


def score_domains(text: str, allowed: Iterable[str]) -> dict[str, int]:
    blob = text or ""
    allowed_set = set(allowed)
    scores: dict[str, int] = {d: 0 for d in allowed_set}
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        if domain not in allowed_set:
            continue
        for kw in keywords:
            if kw and kw in blob:
                scores[domain] += 1
    return scores


def infer_domain(
    *,
    title: str = "",
    description: str = "",
    report_content: str = "",
    project_name: str = "",
    product: str = "",
    department: str = "",
    allowed_domains: Iterable[str] | None = None,
    project_markers: Iterable[tuple[str, str]] | None = None,
) -> Optional[str]:
    """判定单一主领域。判不出返回 None。

    优先级：显式 product → 项目 marker → 部门 → 正文关键词最高分。
    """
    allowed = list(allowed_domains) if allowed_domains is not None else list(DOMAINS)
    if not allowed:
        return None
    allowed_set = set(allowed)

    # 1) 显式产品
    domain = product_to_domain(product)
    if domain in allowed_set:
        return domain

    # 2) 项目 → 产品 → 领域
    inferred_product = infer_product_from_project(project_name, project_markers)
    domain = product_to_domain(inferred_product)
    if domain in allowed_set:
        return domain

    # 3) 部门
    dept_domain = DEPT_DOMAIN.get((department or "").strip(), "")
    if dept_domain in allowed_set:
        return dept_domain

    # 4) 关键词
    text = "\n".join([title or "", description or "", report_content or "", project_name or ""])
    scores = score_domains(text, allowed)
    best = max(scores.values()) if scores else 0
    if best <= 0:
        return None
    # 并列时按 DOMAINS 固定顺序取第一个，保证稳定
    for name in DOMAINS:
        if name in allowed_set and scores.get(name, 0) == best:
            return name
    return None
