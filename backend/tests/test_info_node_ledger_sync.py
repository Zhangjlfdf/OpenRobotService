"""企业微信台账同步（info_node_ledger_sync_service）纯函数测试 —— 不连库、不调外部服务。

覆盖：台账取值口径（_value_text）、project 行 → 台账列还原（含 adapter 兜底值的跳过）、
列 → 条目（空值/定位列过滤）、分组指位（同名分组 / 台账列名是分组名去限定词）、
未匹配条目的归属建议与备注（同名分组 / 同名但装不下 / 包含关系相近 / 相近的是分组 /
都给不出），以及 build_sync_preview 的三组分桶与元信息（打桩本地上下文）。
"""
import json
from types import SimpleNamespace

from app.modules.admin.services import info_node_ledger_sync_service as sync_service


def _tree():
    """贴近真实模板的一小棵树：分组 / 空字段 / 已有内容字段 / 下拉多选项 / 选项装不下的下拉。"""
    return [
        {
            "id": "r1", "title": "基础信息", "content_type": "text", "value": None, "sort_order": 0,
            "children": [
                {"id": "c1", "title": "客户信息", "content_type": "text", "value": "中力", "sort_order": 0, "children": []},
                {
                    "id": "c2", "title": "项目区域/地点", "content_type": "text", "value": None, "sort_order": 1,
                    "children": [
                        {
                            "id": "c21", "title": "区域选项", "content_type": "select", "sort_order": 0,
                            "value": json.dumps(
                                {"selected": "", "options": ["大陆(China Mainland)", "亚洲其他"]},
                                ensure_ascii=False,
                            ),
                            "children": [],
                        },
                    ],
                },
                {
                    "id": "c3", "title": "项目类型", "content_type": "select", "sort_order": 2,
                    "value": json.dumps({"selected": "", "options": ["试点项目", "推广项目"]}, ensure_ascii=False),
                    "children": [],
                },
            ],
        },
        {
            "id": "r2", "title": "人员信息", "content_type": "text", "value": None, "sort_order": 1,
            "children": [
                {"id": "p1", "title": "销售", "content_type": "text", "value": None, "sort_order": 0, "children": []},
                {"id": "p2", "title": "实施", "content_type": "text", "value": "老王", "sort_order": 1, "children": []},
            ],
        },
    ]


def _flat():
    return sync_service.import_service.flatten_tree(_tree())


def _row(**overrides):
    """project 行（只带用例关心的列；_ledger_values 是 getattr 读的，用 SimpleNamespace 就够）。"""
    fields = {
        "name": "江苏南京本川XSC仓储项目", "code": "69",
        "status": "即将进场", "recent_delivery_date": "2026-09-19 11:20",
        "project_region": "大陆（China Mainland）", "sales": "张三",
        "field_engineer": "赵六", "project_type": "普通项目",
        "total_vehicle_count": 6, "undertake_status": "是",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _values(**overrides):
    """_ledger_values 的输出形态（台账列名 → 值），含定位列与空列。"""
    values = {
        "项目编号": "69",
        "项目名称": "江苏南京本川XSC仓储项目",
        "更新时间": "2026-09-19 11:20",
        "销售": "张三",
        "实施工程师": "赵六",
        "项目区域": "大陆（China Mainland）",
        "项目类型": "普通项目",
        "总车数": 6,
        "是否承接": True,
        "附件示例": [{"url": "https://x/a.png"}],
        "空字段示例": "",
    }
    values.update(overrides)
    return values


# —— 取值口径 ——

def test_value_text_matches_wecom_adapter():
    assert sync_service._value_text(None) == ""
    assert sync_service._value_text("  张三  ") == "张三"
    assert sync_service._value_text(True) == "是"
    assert sync_service._value_text(False) == "否"
    assert sync_service._value_text(6) == "6"
    # 选项/成员类字段是 list，取首个非空字符串
    assert sync_service._value_text(["张三", "李四"]) == "张三"
    assert sync_service._value_text(["", "李四"]) == "李四"
    # 图片/附件/位置这类结构化值没有可写进节点的文本
    assert sync_service._value_text([{"url": "https://x/a.png"}]) == ""
    assert sync_service._value_text({"text": "会议室A"}) == ""
    assert sync_service._value_text([]) == ""


# —— project 行 → 台账列 ——

def test_ledger_values_restores_ledger_column_names():
    values = sync_service._ledger_values(_row())
    # 字段名换回台账列名——比对靠的是这个名字，错一个字就整列匹配不上
    assert values["项目名称"] == "江苏南京本川XSC仓储项目"
    assert values["项目生命周期"] == "即将进场"
    assert values["更新时间"] == "2026-09-19 11:20"
    assert values["项目区域"] == "大陆（China Mainland）"
    assert values["是否承接"] == "是"
    assert values["总车数"] == 6
    # 行里没值的列不出现（None 与空串等价：都没什么可同步的）
    assert "承接描述" not in values and "部署版本" not in values
    # 加工过的字段不还原成台账列：「项目类型」只认原文那一列
    assert "category_basis" not in values and values["项目类型"] == "普通项目"


def test_ledger_values_skips_adapter_empty_fallbacks():
    # adapter 对空的「项目生命周期」列兜底成「待开始」——不是台账原文，别同步进节点
    assert "项目生命周期" not in sync_service._ledger_values(_row(status="待开始"))
    # 其余状态值都是台账原样落库的（STATUS_MAP 是同义词表），照旧带上
    assert sync_service._ledger_values(_row(status="正在实施"))["项目生命周期"] == "正在实施"


def test_ledger_items_skips_empty_and_locator_columns():
    items = sync_service._ledger_items(_values())
    titles = [item["title"] for item in items]
    # 定位列（项目编号/项目名称）不是项目信息、空字段没什么可同步
    assert titles == ["更新时间", "销售", "实施工程师", "项目区域", "项目类型", "总车数", "是否承接"]
    assert "项目编号" not in titles and "项目名称" not in titles
    assert "空字段示例" not in titles and "附件示例" not in titles

    first = items[0]
    assert first == {
        "title": "更新时间", "value": "2026-09-19 11:20",
        "node_title": None, "quantity": None, "suggested_parent_path": None,
    }
    # 布尔列按中文落进节点
    assert next(item for item in items if item["title"] == "是否承接")["value"] == "是"


# —— 分组指位 ——

def test_find_value_group_prefers_exact_name_then_containment():
    flat = _flat()
    # 同名分组
    same = sync_service._find_value_group(flat, "项目区域/地点")
    assert same is not None and same["id"] == "c2"
    # 台账列名是分组名去掉了限定词（项目区域 ⊂ 项目区域/地点）
    near = sync_service._find_value_group(flat, "项目区域")
    assert near is not None and near["id"] == "c2"
    # 同名但能填值的节点不是分组：它自己就是归属，不用另找
    assert sync_service._find_value_group(flat, "销售") is None
    # 反向不认：分组名比列名短，那更像另一件事
    assert sync_service._find_value_group(flat, "项目区域/地点/省份") is None


def test_pin_group_values_points_at_dropdown_child():
    flat = _flat()
    items = sync_service._ledger_items(_values())
    sync_service._pin_group_values(flat, items)

    # 「项目区域」是分组名去限定词 → 值指到分组下唯一装得下它的下拉「区域选项」
    # （台账值是全角括号，选项是半角：规范化后相等）
    pinned = next(item for item in items if item["title"] == "项目区域")
    assert pinned["node_title"] == "区域选项"
    # 选项装不下的（项目类型没有「普通项目」）不动，交给 match_items 与备注
    assert next(item for item in items if item["title"] == "项目类型")["node_title"] is None
    # 非分组的同名列不看：普通字段自己就是能填值的节点
    assert next(item for item in items if item["title"] == "销售")["node_title"] is None


def test_dropdown_child_taking_only_accepts_a_single_candidate():
    flat = _flat()
    group = next(node for node in flat if node["title"] == "项目区域/地点")
    assert sync_service._dropdown_child_taking(flat, group, "大陆(China Mainland)")["id"] == "c21"
    # 值不在可选项里 → 不给（宁可不猜）
    assert sync_service._dropdown_child_taking(flat, group, "火星") is None

    # 两个下拉都装得下这个值 → 无从判断是给谁的，也不给
    flat2 = flat + [{
        "id": "c22", "parent_id": group["id"], "title": "部署区域", "content_type": "select",
        "value": None, "options": ["大陆(China Mainland)", "亚洲其他"], "depth": 3,
        "path": "基础信息 / 项目区域/地点 / 部署区域", "path_titles": [], "has_children": False,
    }]
    assert sync_service._dropdown_child_taking(flat2, group, "大陆(China Mainland)") is None


# —— 未匹配条目的归属建议与备注 ——

def test_enrich_unmatched_suggests_group_parent():
    flat = _flat()
    row = {"title": "项目区域/地点", "value": "火星", "suggested_parent_id": None, "suggested_parent_path": None}
    sync_service._enrich_unmatched(flat, row)
    assert row["suggested_parent_id"] == "c2"
    assert row["suggested_parent_path"] == "基础信息 / 项目区域/地点"
    assert "同名分组节点" in row["note"]


def test_enrich_unmatched_explains_select_option_gap():
    flat = _flat()
    row = {"title": "项目类型", "value": "普通项目", "suggested_parent_id": None, "suggested_parent_path": None}
    sync_service._enrich_unmatched(flat, row)
    # 不给归属：前端落到「导入信息」兜底，并由备注说清是选项的问题
    assert row["suggested_parent_id"] is None
    assert row["suggested_parent_path"] is None
    assert "下拉" in row["note"] and "补上选项" in row["note"]


def test_enrich_unmatched_suggests_vicinity_by_containment():
    flat = _flat()
    # 「实施工程师」树里只有「实施」：包含关系相近 → 挂到「实施」所在的层级下（人员信息）
    row = {"title": "实施工程师", "value": "赵六", "suggested_parent_id": None, "suggested_parent_path": None}
    sync_service._enrich_unmatched(flat, row)
    assert row["suggested_parent_id"] == "r2"
    assert row["suggested_parent_path"] == "人员信息"
    assert row.get("note") is None

    # 根节点下的相近节点 → 建议挂在根节点自己下面
    row2 = {"title": "基础信息补充", "value": "x", "suggested_parent_id": None, "suggested_parent_path": None}
    sync_service._enrich_unmatched(flat, row2)
    assert row2["suggested_parent_id"] == "r1"

    # 相近的本身就是分组（项目区域 vs 项目区域/地点）→ 建议落在分组里面，并说明原因
    row3 = {"title": "项目区域", "value": "火星", "suggested_parent_id": None, "suggested_parent_path": None}
    sync_service._enrich_unmatched(flat, row3)
    assert row3["suggested_parent_id"] == "c2"
    assert row3["suggested_parent_path"] == "基础信息 / 项目区域/地点"
    assert "分组节点" in row3["note"]

    # 包含关系也没有（「是否承接」不该被「是否对接」之类的相似度吸走）→ 不给建议
    row4 = {"title": "是否承接", "value": "是", "suggested_parent_id": None, "suggested_parent_path": None}
    sync_service._enrich_unmatched(flat, row4)
    assert row4["suggested_parent_id"] is None
    assert row4["suggested_parent_path"] is None


# —— 整条预览 ——

def _stub(project, values, flat):
    """把本地上下文换成打桩（不打补丁到 match_items：分桶要真实走一遍）。"""
    sync_service._load_local_context = lambda project_id: (project, values, flat)


def _restore(original):
    sync_service._load_local_context = original


def test_build_sync_preview_buckets_and_meta():
    original = sync_service._load_local_context
    project = {"id": "69", "code": "69", "name": "江苏南京本川XSC仓储项目"}
    try:
        _stub(project, _values(), _flat())
        result = sync_service.build_sync_preview("69")
    finally:
        _restore(original)

    # 元信息：台账更新时间（镜像列）+ 参与比对的字段数 + 镜像的列总数
    assert result["project_code"] == "69"
    assert result["ledger_updated_at"] == "2026-09-19 11:20"
    assert result["field_count"] == 7
    assert result["mirror_field_total"] == len(sync_service.PROJECT_LEDGER_FIELDS)

    # 将填写：空节点（「销售」），「项目区域」经分组指位落到「区域选项」
    filled = {row["node_id"]: row["value"] for row in result["fill"]}
    assert filled["p1"] == "张三"
    assert filled["c21"] == "大陆(China Mainland)"
    assert result["overwrite"] == []          # 本次台账值与现有内容不冲突

    # 未匹配：树里没有的列，带上归属建议或说明
    unmatched = {row["title"]: row for row in result["unmatched"]}
    assert "实施工程师" in unmatched and unmatched["实施工程师"]["suggested_parent_id"] == "r2"
    assert "项目类型" in unmatched and "补上选项" in unmatched["项目类型"]["note"]
    assert "总车数" in unmatched and "是否承接" in unmatched


def test_build_sync_preview_reports_conflicts_as_overwrite():
    original = sync_service._load_local_context
    project = {"id": "69", "code": "69", "name": "江苏南京本川XSC仓储项目"}
    # 把树里「销售」「实施」填上内容，台账值不同 → 矛盾（将覆盖）而不是将填写
    flat = _flat()
    for node in flat:
        if node["id"] == "p1":
            node["value"] = "旧销售"
        if node["id"] == "p2":
            node["value"] = "老王"
    try:
        _stub(project, _values(实施="老王"), flat)
        result = sync_service.build_sync_preview("69")
    finally:
        _restore(original)

    overwritten = {row["node_id"]: (row["current"], row["value"]) for row in result["overwrite"]}
    assert overwritten["p1"] == ("旧销售", "张三")     # 矛盾：原内容 → 台账值，等用户点头
    assert all(row["node_id"] != "p2" for row in result["overwrite"])   # 与台账一致 → 不产生变更


def test_build_sync_preview_rejects_project_without_nodes():
    original = sync_service._load_local_context
    project = {"id": "69", "code": "69", "name": "空树项目"}
    try:
        _stub(project, {}, [])
        try:
            sync_service.build_sync_preview("69")
        except ValueError as exc:
            assert "还没有信息节点" in str(exc)
        else:
            raise AssertionError("空树项目应当报 ValueError（接口层 400）")
    finally:
        _restore(original)
