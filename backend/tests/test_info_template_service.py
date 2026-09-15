"""详情模板服务纯函数测试 —— 不连库：校验规范化、树展平、同步差异计算。

同步差异（compute_sync_plan）是「模板改动 → 各项目节点变更」的核心：
回填锚点 / 新建 / 更新（标题·父子·顺序·内容类型·下拉选项）/ 删除（锚点失效的子树，
锚点仍有效的节点会被移走而非删除）。
"""
import json

from app.modules.admin.services.info_template_service import (
    compute_sync_plan,
    flatten_template,
    normalize_template_nodes,
)


def _tpl():
    """一份贴近真实模板的两级结构（含 select 与 4 层深的车型分支）。"""
    return [
        {
            "id": "t-base", "title": "基础信息", "content_type": "text", "sort_order": 0,
            "children": [
                {"id": "t-cust", "title": "客户信息", "content_type": "text", "sort_order": 0, "children": []},
                {
                    "id": "t-type", "title": "项目类型", "content_type": "select",
                    "options": ["试点项目", "PK项目"], "sort_order": 1, "children": [],
                },
            ],
        },
        {
            "id": "t-hw", "title": "硬件", "content_type": "text", "sort_order": 1,
            "children": [
                {
                    "id": "t-veh", "title": "车辆", "content_type": "text", "sort_order": 0,
                    "children": [
                        {"id": "t-m1", "title": "车型1", "content_type": "text", "sort_order": 0, "children": []},
                    ],
                },
            ],
        },
    ]


def _pn(node_id, title, *, parent=None, ct="text", value=None, sort=0, tpl=None):
    return {
        "id": node_id, "parent_id": parent, "title": title, "content_type": ct,
        "value": value, "sort_order": sort, "template_node_id": tpl,
    }


# ── 校验规范化 ────────────────────────────────────────────

def test_normalize_ok():
    nodes = normalize_template_nodes([
        {"id": "a", "title": " 基础信息 ", "children": [
            {"id": "b", "title": "客户信息"},
            {"id": "c", "title": "项目类型", "options": ["试点项目", " PK项目 "]},
        ]},
    ])
    assert nodes[0]["title"] == "基础信息" and nodes[0]["sort_order"] == 0
    assert nodes[0]["content_type"] == "text"
    child = nodes[0]["children"]
    assert child[0]["content_type"] == "text"
    assert child[1]["content_type"] == "select"
    assert child[1]["options"] == ["试点项目", "PK项目"]  # 选项去空白
    assert child[1]["sort_order"] == 1


def test_normalize_rejects_bad_templates():
    cases = [
        ([], "模板不能为空"),
        ([{"title": "无id"}], "缺少 id"),
        ([{"id": "a", "title": "  "}], "标题不能为空"),
        ([{"id": "a", "title": "x"}, {"id": "a", "title": "y"}], "id 重复"),
        ([{"id": "a", "title": "下拉", "content_type": "select", "children": [{"id": "b", "title": "子"}]}], "不能有子节点"),
    ]
    for nodes, keyword in cases:
        try:
            normalize_template_nodes(nodes)
        except ValueError as exc:
            assert keyword in str(exc), f"{keyword} 未出现在：{exc}"
        else:
            raise AssertionError(f"应拒绝：{keyword}")

    # 第 5 层超深
    deep = {"id": "l4", "title": "第四层", "children": [{"id": "l5", "title": "第五层"}]}
    tree = [{"id": "l1", "title": "一", "children": [{"id": "l2", "title": "二", "children": [{"id": "l3", "title": "三", "children": [deep]}]}]}]
    try:
        normalize_template_nodes(tree)
    except ValueError as exc:
        assert "层" in str(exc)
    else:
        raise AssertionError("超过 4 层应被拒绝")


# ── 树展平 ────────────────────────────────────────────────

def test_flatten_template_paths_depths():
    flat = flatten_template(normalize_template_nodes(_tpl()))
    by_id = {n["id"]: n for n in flat}
    assert by_id["t-m1"]["path"] == "硬件 / 车辆 / 车型1"
    assert by_id["t-m1"]["depth"] == 3
    assert by_id["t-m1"]["parent_id"] == "t-veh"
    assert by_id["t-type"]["content_type"] == "select"
    assert by_id["t-type"]["options"] == ["试点项目", "PK项目"]
    # 父节点在子节点之前（同步按序创建/更新）
    assert [n["id"] for n in flat].index("t-hw") < [n["id"] for n in flat].index("t-veh")


# ── 同步差异计算 ──────────────────────────────────────────

def _flat():
    return flatten_template(normalize_template_nodes(_tpl()))


def test_plan_links_existing_nodes_by_path():
    """存量项目（无锚点）：按标题路径回填锚点；路径对不上的用户自建节点不受影响。"""
    project_nodes = [
        _pn("p1", "基础信息", sort=0),
        _pn("p2", "客户信息", parent="p1", value="中力", sort=0),
        _pn("p3", "项目类型", parent="p1", ct="select",
            value=json.dumps({"selected": "试点项目", "options": ["试点项目", "PK项目"]}, ensure_ascii=False), sort=1),
        _pn("p4", "用户自建", parent="p1", sort=2),
    ]
    plan = compute_sync_plan(_flat(), project_nodes)
    linked = {item["project_node_id"]: item["template_node_id"] for item in plan["link"]}
    assert linked == {"p1": "t-base", "p2": "t-cust", "p3": "t-type"}
    # 自建节点不回填、不更新、不删除
    assert all(item["node_id"] != "p4" for item in plan["updates"])
    assert "p4" not in plan["delete_ids"]
    # 已勾选项仍在模板选项里 → 值不重写
    assert all(item["node_id"] != "p3" for item in plan["updates"])
    # 模板新增的节点（硬件分支）会新建
    created_tpl = {item["template_node_id"] for item in plan["creates"]}
    assert created_tpl == {"t-hw", "t-veh", "t-m1"}
    # 新建节点挂在正确父级下（t-m1 的父是本次新建的 t-veh 节点）
    by_tpl = {item["template_node_id"]: item for item in plan["creates"]}
    assert by_tpl["t-hw"]["parent_id"] is None
    assert by_tpl["t-veh"]["parent_id"] == by_tpl["t-hw"]["node_id"]
    assert by_tpl["t-m1"]["parent_id"] == by_tpl["t-veh"]["node_id"]


def test_plan_updates_title_parent_sort_and_type():
    """锚点对上的节点：标题/父子/顺序/内容类型以模板为准；text→select 重置值。"""
    project_nodes = [
        _pn("p1", "基础信息（旧名）", tpl="t-base", sort=5),
        _pn("p2", "客户信息", parent="p1", tpl="t-cust", value="中力", sort=0),
        _pn("p3", "项目类型", tpl="t-type", ct="text", value="试点项目", sort=1),  # 类型从 text 改为 select
        _pn("p9", "车型1", tpl="t-m1", sort=3),  # 位置错了：模板里在 硬件/车辆 下
    ]
    plan = compute_sync_plan(_flat(), project_nodes)
    updates = {item["node_id"]: item["changes"] for item in plan["updates"]}

    assert updates["p1"] == {"title": "基础信息", "sort_order": 0}
    assert "p2" not in updates or updates["p2"] == {}  # 已一致
    # 内容类型变化 → 值按模板重置为下拉空值
    assert updates["p3"]["content_type"] == "select"
    assert json.loads(updates["p3"]["value"]) == {"selected": "", "options": ["试点项目", "PK项目"]}
    # 车型1 需要被移动到新建的 硬件/车辆 下（新建节点 id 从 creates 里取）
    by_tpl = {item["template_node_id"]: item for item in plan["creates"]}
    assert updates["p9"]["parent_id"] == by_tpl["t-veh"]["node_id"]
    assert updates["p9"]["sort_order"] == 0


def test_plan_merges_select_options_keeping_valid_selection():
    project_nodes = [
        _pn("p3", "项目类型", tpl="t-type", ct="select",
            value=json.dumps({"selected": "试点项目", "options": ["试点项目"]}, ensure_ascii=False)),
    ]
    plan = compute_sync_plan(_flat(), project_nodes)
    updates = {item["node_id"]: item["changes"] for item in plan["updates"]}
    merged = json.loads(updates["p3"]["value"])
    assert merged["selected"] == "试点项目"  # 仍在新选项里 → 保留
    assert merged["options"] == ["试点项目", "PK项目"]

    # 已选项不在新选项里 → 清空选择
    project_nodes[0]["value"] = json.dumps({"selected": "大客户项目", "options": ["大客户项目"]}, ensure_ascii=False)
    plan = compute_sync_plan(_flat(), project_nodes)
    updates = {item["node_id"]: item["changes"] for item in plan["updates"]}
    assert json.loads(updates["p3"]["value"])["selected"] == ""


def test_plan_deletes_stale_anchors_but_spares_alive_descendants():
    """模板删掉的锚点：删节点及子树；子树里锚点仍有效的节点会被移走，不删。"""
    project_nodes = [
        # p-old 的锚点已不在模板里 → 整棵删（p-old-child 也是失效锚点）
        _pn("p-old", "废弃分组", tpl="t-gone", sort=0),
        _pn("p-old-child", "废子项", parent="p-old", tpl="t-gone-child", sort=0),
        # p-veh 的锚点在模板里仍有效，但当前挂在废弃分组下 → 被移动而不是删除
        _pn("p-veh", "车辆", parent="p-old", tpl="t-veh", sort=1),
        _pn("p-hw", "硬件", tpl="t-hw", sort=2),
    ]
    plan = compute_sync_plan(_flat(), project_nodes)
    assert plan["delete_ids"] == {"p-old", "p-old-child"}
    updates = {item["node_id"]: item["changes"] for item in plan["updates"]}
    assert updates["p-veh"]["parent_id"] == "p-hw"  # 移回模板位置（模板里 车辆 在 硬件 下）
    assert plan["creates"] == [] or all(c["template_node_id"] != "t-veh" for c in plan["creates"])


def test_plan_noop_when_in_sync():
    """项目节点与模板完全一致 → 无任何动作。"""
    project_nodes = [
        _pn("p1", "基础信息", tpl="t-base", sort=0),
        _pn("p2", "客户信息", parent="p1", tpl="t-cust", sort=0),
        _pn("p3", "项目类型", parent="p1", tpl="t-type", ct="select",
            value=json.dumps({"selected": "", "options": ["试点项目", "PK项目"]}, ensure_ascii=False), sort=1),
        _pn("p4", "硬件", tpl="t-hw", sort=1),
        _pn("p5", "车辆", parent="p4", tpl="t-veh", sort=0),
        _pn("p6", "车型1", parent="p5", tpl="t-m1", sort=0),
    ]
    plan = compute_sync_plan(_flat(), project_nodes)
    assert plan["creates"] == [] and plan["updates"] == [] and plan["link"] == []
    assert plan["delete_ids"] == set()
