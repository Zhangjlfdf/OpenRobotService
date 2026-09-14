"""AI 数据分析平台 · 图表构建与数据本地化

把 ``ReportDataCollector.collect_by_plan()`` 的采集结果转换为：

1. 前端可直接渲染的图表/指标卡片（:class:`ChartSpec` / :class:`MetricCard`），
   数据全部来自采集结果，LLM 不参与图表数据生成，杜绝编造；
2. 喂给 LLM 的中文化数据（根除英文字段名泄漏问题）。

字段 → 中文映射表（``FIELD_LABEL_MAP``）是 localize 与 sanitize 的唯一来源，
避免两处维护。
"""

from __future__ import annotations

import re

from .metric_registry import MetricOutputType, get_metric_def
from .schemas import ChartSpec, MetricCard

# ── 字段名 → 中文映射（localize 与 sanitize 共用）──────────────────

# 采集结果内部字段 → 中文。外层维度 key（ticket/project/risk/date_range）
# 与单值常见英文单词（total/items 等）仅用于 localize，不参与 sanitize
# 兜底替换（避免误伤正常英文表述）。
FIELD_LABEL_MAP: dict[str, str] = {
    "date_range": "统计周期",
    "ticket": "工单",
    "project": "项目",
    "risk": "风险",
    "total": "总数",
    "new_count": "新增数",
    "resolved_count": "已解决数",
    "closed_count": "已关闭数",
    "resolve_rate": "解决率",
    "overdue_count": "逾期数",
    "active_count": "活跃数",
    "completed_count": "已完成数",
    "on_hold_count": "暂停数",
    "by_status": "状态分布",
    "by_priority": "优先级分布",
    "by_type": "类型分布",
    "by_level": "等级分布",
    "by_category": "分类分布",
    "new_by_day": "每日新增",
    "overdue_list": "逾期明细",
    "items": "明细列表",
}

# 百分比类指标字段（value 为 0~100 的数值，卡片展示时带 % 单位）
_PERCENT_FIELDS: frozenset[str] = frozenset({"resolve_rate"})

# sanitize 兜底替换白名单：仅替换含下划线的字段名（如 new_count），
# 单词字段（total/items/risk 等）是常见英文词，替换会误伤正常表述。
_SANITIZE_FIELDS: tuple[str, ...] = tuple(
    field for field in FIELD_LABEL_MAP if "_" in field
)


# ── 图表构建 ──────────────────────────────────────────────────────


def build_charts(
    metric_keys: list[str], collected: dict
) -> tuple[list[ChartSpec], list[MetricCard]]:
    """按指标白名单把采集结果转换为图表与卡片。

    Args:
        metric_keys: 本轮的指标 key 列表（与 plan.metric_keys 一致）。
        collected: ``collect_by_plan()`` 的返回 dict。

    Returns:
        (charts, cards)：分布指标 → 图表，单值指标 → 卡片；
        明细列表指标（LIST）不配图不配卡。
    """
    charts: list[ChartSpec] = []
    cards: list[MetricCard] = []

    for key in metric_keys:
        metric = get_metric_def(key)
        if metric is None:
            continue
        dim_data = collected.get(metric.dimension.value) or {}
        field = key.split(".", 1)[1]
        if field not in dim_data:
            continue
        value = dim_data[field]

        if metric.output_type == MetricOutputType.SCALAR:
            cards.append(_build_card(metric.label, field, value))
        elif metric.output_type == MetricOutputType.DISTRIBUTION and isinstance(value, dict):
            charts.append(_build_distribution_chart(metric.label, value))
        elif metric.output_type == MetricOutputType.TREND and isinstance(value, dict):
            charts.append(_build_trend_chart(metric.label, value))
        # LIST（明细）不配图，交给 LLM 用文字表格呈现

    return charts, cards


def _build_card(label: str, field: str, value: int | float) -> MetricCard:
    """单值指标 → 指标卡片。百分比类带 % 单位，计数类为纯数字。"""
    if field in _PERCENT_FIELDS:
        # 去尾零：85.0 → "85"，90.5 → "90.5"
        return MetricCard(label=label, value=f"{float(value):g}", unit="%", kind="metric")
    return MetricCard(label=label, value=str(int(value)), unit=None, kind="count")


def _build_distribution_chart(label: str, dist: dict) -> ChartSpec:
    """分布 dict（键已为中文）→ 饼图（≤6 类）或柱状图（>6 类）。"""
    items = sorted(dist.items(), key=lambda kv: kv[1], reverse=True)
    labels = [k for k, _ in items]
    values = [v for _, v in items]

    if len(labels) <= 6:
        return ChartSpec(
            chart_type="pie",
            title=label,
            option={
                "tooltip": {"trigger": "item", "formatter": "{b}: {c}（{d}%）"},
                "legend": {"bottom": 0, "type": "scroll", "textStyle": {"fontSize": 11}},
                "series": [
                    {
                        "type": "pie",
                        "radius": ["38%", "62%"],
                        "center": ["50%", "44%"],
                        "label": {"show": False},
                        "data": [{"name": k, "value": v} for k, v in items],
                    }
                ],
            },
        )
    return ChartSpec(
        chart_type="bar",
        title=label,
        option={
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 8, "right": 8, "top": 24, "bottom": 24, "containLabel": True},
            "xAxis": {
                "type": "category",
                "data": labels,
                "axisLabel": {"interval": 0, "rotate": 30, "fontSize": 10},
            },
            "yAxis": {"type": "value", "minInterval": 1},
            "series": [
                {
                    "type": "bar",
                    "data": values,
                    "itemStyle": {"color": "#3b82f6", "borderRadius": [4, 4, 0, 0]},
                }
            ],
        },
    )


def _build_trend_chart(label: str, trend: dict) -> ChartSpec:
    """趋势 dict（日期为键）→ 折线图。"""
    days = sorted(trend.keys())
    values = [trend[d] for d in days]
    return ChartSpec(
        chart_type="line",
        title=label,
        option={
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 8, "right": 8, "top": 24, "bottom": 24, "containLabel": True},
            "xAxis": {"type": "category", "data": days, "axisLabel": {"fontSize": 10}},
            "yAxis": {"type": "value", "minInterval": 1},
            "series": [
                {
                    "type": "line",
                    "data": values,
                    "smooth": True,
                    "symbolSize": 5,
                    "lineStyle": {"width": 2},
                    "itemStyle": {"color": "#3b82f6"},
                    "areaStyle": {"opacity": 0.12},
                }
            ],
        },
    )


# ── 数据本地化（喂 LLM）────────────────────────────────────────────


def localize_collected_data(collected: dict) -> dict:
    """把采集结果转成中文 key 的 dict，供 LLM 分析使用。

    递归替换 dict 的 key（字段名），value 原样保留；
    list 元素递归处理（items 明细中的中文 key 原样保留）。
    """
    def _translate(value):
        if isinstance(value, dict):
            return {FIELD_LABEL_MAP.get(k, k): _translate(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_translate(v) for v in value]
        return value

    return _translate(collected)


def sanitize_field_names(text: str) -> str:
    """兜底替换 LLM 回答中残留的英文字段名（仅含下划线的字段）。

    如 ``new_count`` → ``新增数``、``resolve_rate`` → ``解决率``；
    单词字段（total/items/risk 等）不替换，避免误伤正常英文表述。
    """
    for field in _SANITIZE_FIELDS:
        text = re.sub(rf"\b{re.escape(field)}\b", FIELD_LABEL_MAP[field], text)
    return text
