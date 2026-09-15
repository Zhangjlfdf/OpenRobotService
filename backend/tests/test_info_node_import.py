"""文件导入（AI 识别）服务纯函数测试 —— 不连库、不调大模型。

覆盖：文本抽取（docx/xlsx/md/txt 与坏文件）、节点清单与提示词拼装、
大模型输出解析容错、节点匹配（精确/相似度 0.9/下拉选项/去重）与建议归属解析。
"""
import io
import json
import zipfile

from app.modules.admin.services.info_node_import_service import (
    SIMILARITY_THRESHOLD,
    VEHICLE_MODEL_SERIES,
    build_import_prompt,
    build_node_catalog,
    build_vehicle_model_catalog,
    extract_text,
    find_vehicle_parent_path,
    flatten_tree,
    match_items,
    parse_llm_items,
    parse_llm_payload,
    project_name_mismatch,
    resolve_parent,
)

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _tree():
    """一份贴近真实模板的小信息树（含空节点/已有内容节点/下拉/深层级）。"""
    return [
        {
            "id": "r1", "title": "基础信息", "content_type": "text", "value": None, "sort_order": 0,
            "children": [
                {"id": "c1", "title": "客户信息", "content_type": "text", "value": "中力", "sort_order": 0, "children": []},
                {
                    "id": "c2", "title": "订单信息", "content_type": "text", "value": "", "sort_order": 1,
                    "children": [
                        {
                            "id": "c21", "title": "ERP", "content_type": "text", "value": None, "sort_order": 0,
                            "children": [
                                {"id": "c211", "title": "ERP模块", "content_type": "text", "value": "", "sort_order": 0, "children": []},
                            ],
                        },
                    ],
                },
                {
                    "id": "c3", "title": "项目类型", "content_type": "select", "sort_order": 2,
                    "value": json.dumps({"selected": "", "options": ["试点项目", "PK项目"]}, ensure_ascii=False),
                    "children": [],
                },
            ],
        },
        {
            "id": "r2", "title": "网络信息", "content_type": "text", "value": None, "sort_order": 1,
            "children": [
                {"id": "n1", "title": "公网ip", "content_type": "text", "value": "10.0.0.1", "sort_order": 0, "children": []},
                {"id": "n2", "title": "通道与托盘间距尺寸", "content_type": "text", "value": "", "sort_order": 1, "children": []},
            ],
        },
    ]


def _item(title, value, node_title=None, parent_path=None):
    return {"title": title, "value": value, "node_title": node_title, "suggested_parent_path": parent_path}


# ── 文本抽取 ──────────────────────────────────────────────

def test_extract_text_plain_and_gbk():
    assert extract_text("需求.md", "# 项目需求\n客户：中力".encode("utf-8")) == "# 项目需求\n客户：中力"
    # 中文文档常见的 GBK 编码
    assert extract_text("需求.txt", "客户信息：中力".encode("gbk")) == "客户信息：中力"


def test_extract_text_rejects_unsupported_and_empty():
    for name in ("a.json", "a.pdf", "无扩展名"):
        try:
            extract_text(name, b"x")
        except ValueError:
            continue
        raise AssertionError(f"{name} 应被拒绝")
    for name in ("a.doc", "a.xls"):
        try:
            extract_text(name, b"x")
        except ValueError as exc:
            assert "另存为" in str(exc)
        else:
            raise AssertionError(f"{name} 旧格式应提示另存为新格式")
    try:
        extract_text("a.txt", b"   ")
    except ValueError as exc:
        assert "没有" in str(exc)
    else:
        raise AssertionError("空内容应被拒绝")


def _docx_bytes(paragraphs, table_rows=None):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    if table_rows:
        rows = "".join(
            "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>" for cell in row) + "</w:tr>"
            for row in table_rows
        )
        body += f"<w:tbl>{rows}</w:tbl>"
    document = f'<?xml version="1.0"?><w:document xmlns:w="{_W_NS}"><w:body>{body}</w:body></w:document>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def test_extract_text_docx_paragraphs_and_table():
    text = extract_text("方案.docx", _docx_bytes(["项目需求说明", "客户：中力"], [["电梯", "厂家A"], ["自动门", ""]]))
    lines = text.splitlines()
    assert lines[0] == "项目需求说明"
    assert lines[1] == "客户：中力"
    assert "电梯 | 厂家A" in lines
    assert "自动门 |" in lines  # 空单元格保留列位


def test_extract_text_docx_invalid():
    try:
        extract_text("坏.docx", b"not-a-zip")
    except ValueError as exc:
        assert ".doc" in str(exc)
    else:
        raise AssertionError("坏 docx 应被拒绝")


def test_extract_text_xlsx_sheets():
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "汇总"
    sheet.append(["客户信息", "中力"])
    sheet.append([None, None])  # 空行跳过
    sheet.append(["公网ip", "10.0.0.1"])
    second = book.create_sheet("明细")
    second.append(["型号", "数量"])
    buffer = io.BytesIO()
    book.save(buffer)

    text = extract_text("台账.xlsx", buffer.getvalue())
    assert "# 工作表：汇总" in text
    assert "客户信息\t中力" in text
    assert "# 工作表：明细" in text
    assert "型号\t数量" in text


# ── 节点清单与提示词 ───────────────────────────────────────

def test_flatten_tree_paths_depths():
    flat = flatten_tree(_tree())
    by_id = {n["id"]: n for n in flat}
    assert by_id["c21"]["path"] == "基础信息 / 订单信息 / ERP"
    assert by_id["c21"]["depth"] == 3
    assert by_id["c211"]["depth"] == 4
    assert by_id["c1"]["has_children"] is False
    assert by_id["r1"]["has_children"] is True


def test_build_catalog_and_prompt():
    flat = flatten_tree(_tree())
    catalog = build_node_catalog(flat)
    # 末级节点标注 (末级)，非末级（ERP 带子节点）不标
    assert "基础信息 / 客户信息\ttext\t(末级)" in catalog
    assert "基础信息 / 订单信息 / ERP\ttext\n" in catalog + "\n"
    assert "可选项：试点项目|PK项目" in catalog

    prompt = build_import_prompt(catalog, "客户：中力", "中力越南项目")
    assert catalog in prompt and "客户：中力" in prompt
    assert f"{SIMILARITY_THRESHOLD}" in prompt  # 提示词明确了 0.9 的匹配阈值
    assert '"items"' in prompt
    # 项目名校对：目标项目名进提示词，并要求回传文件自带的 projectName
    assert "中力越南项目" in prompt and "projectName" in prompt


def _vehicle_tree():
    """真实模板片段：硬件 / 车辆 / 车型1 / 数量。"""
    return [
        {
            "id": "h1", "title": "硬件", "content_type": "text", "value": None, "sort_order": 0,
            "children": [
                {
                    "id": "v1", "title": "车辆", "content_type": "text", "value": None, "sort_order": 0,
                    "children": [
                        {
                            "id": "m1", "title": "车型1", "content_type": "text", "value": None, "sort_order": 0,
                            "children": [
                                {"id": "q1", "title": "数量", "content_type": "text", "value": "", "sort_order": 0, "children": []},
                            ],
                        },
                    ],
                },
            ],
        },
    ]


def test_vehicle_catalog_completeness():
    # 8 大系列 50 款，型号不重复
    assert len(VEHICLE_MODEL_SERIES) == 8
    codes = [code for _, models in VEHICLE_MODEL_SERIES for code, _ in models]
    assert len(codes) == 50
    assert len(set(codes)) == 50
    # 抽查各系列（含单款系列与具身系列）
    for code in ("XC1051", "EXP15", "XPL201P", "XSC201", "XQS181", "XCART", "XCU0051", "XCB031"):
        assert code in codes
    catalog = build_vehicle_model_catalog()
    assert "潜伏小车系列：XC1051（背负式搬运机器人 500 kg）" in catalog
    assert "具身机器人系列：XCB031" in catalog


def test_find_vehicle_parent_path():
    flat = flatten_tree(_vehicle_tree())
    # 「车型1」的父级是「车辆」→ 车型信息归属到 硬件 / 车辆
    assert find_vehicle_parent_path(flat) == "硬件 / 车辆"
    # 只有「车辆」没有「车型N」→ 归属到车辆节点本身
    bare = flatten_tree([{"id": "v9", "title": "车辆", "content_type": "text", "value": None, "sort_order": 0, "children": []}])
    assert find_vehicle_parent_path(bare) == "车辆"
    # 信息树里没有车辆/车型节点 → None（提示词不注入车型清单）
    assert find_vehicle_parent_path(flatten_tree(_tree())) is None


def test_build_prompt_vehicle_catalog_injection():
    flat = flatten_tree(_vehicle_tree())
    prompt = build_import_prompt(
        build_node_catalog(flat), "现场部署 XCD101 潜伏顶升搬运机器人 2 台", "中力越南项目",
        find_vehicle_parent_path(flat),
    )
    # 车型清单进提示词，归属路径取车型节点的父级，并附车型专用规则
    assert "车型清单" in prompt
    assert "XCD101（潜伏顶升搬运机器人 1000 kg）" in prompt
    assert "suggestedParentPath 填「硬件 / 车辆」" in prompt
    assert "\n8. " in prompt

    # 没有车辆节点的信息树不注入车型清单，也没有第 8 条规则
    plain = build_import_prompt(build_node_catalog(flatten_tree(_tree())), "客户：中力", "中力越南项目")
    assert "车型清单" not in plain and "XC1051" not in plain
    assert "\n8. " not in plain


def test_project_name_mismatch():
    # 规范化后一致（标点/空白差异）→ 不算不一致
    assert project_name_mismatch("中力越南项目", "中力-越南 项目") is False
    # 一方包含另一方 → 不算不一致（如带期数/后缀）
    assert project_name_mismatch("中力越南项目", "中力越南项目（二期）") is False
    assert project_name_mismatch("中力越南AGV项目", "中力越南项目") is False
    # 相似度 ≥0.6（仅少一个词）→ 不算不一致
    assert project_name_mismatch("中力越南项目", "中力越南搬运项目") is False
    # 明显不同的项目名 → 不一致
    assert project_name_mismatch("中力越南项目", "杭叉智能仓储项目") is True
    # 文件里没识别到项目名 / 系统名为空 → 无从比较，不报警
    assert project_name_mismatch("中力越南项目", None) is False
    assert project_name_mismatch("中力越南项目", "") is False
    assert project_name_mismatch("", "杭叉项目") is False


# ── 大模型输出解析 ────────────────────────────────────────

def test_parse_llm_items_strips_fence_and_normalizes():
    content = "```json\n" + json.dumps({
        "items": [
            {"title": "客户信息", "value": " 浙江中力 ", "nodeTitle": "客户信息", "suggestedParentPath": None},
            {"title": "数量", "value": 3, "nodeTitle": None, "suggestedParentPath": "硬件 / 车辆"},
            {"title": "空值条目", "value": "   ", "nodeTitle": None},
            {"title": "", "value": "无标题", "nodeTitle": None},
        ],
    }, ensure_ascii=False) + "\n```"
    items = parse_llm_items(content)
    assert items == [
        {"title": "客户信息", "value": "浙江中力", "node_title": "客户信息", "suggested_parent_path": None},
        {"title": "数量", "value": "3", "node_title": None, "suggested_parent_path": "硬件 / 车辆"},
    ]


def test_parse_llm_items_rejects_garbage():
    for bad in ("完全不是 JSON", "{\"foo\": 1}"):
        try:
            items = parse_llm_items(bad)
        except ValueError:
            continue
        assert items == []  # 合法 JSON 但没有 items → 空列表


def test_parse_llm_payload_project_name():
    # projectName 正常提取（含前后空白清理）
    payload = parse_llm_payload(json.dumps({
        "projectName": " 中力越南项目 ",
        "items": [{"title": "客户信息", "value": "中力"}],
    }, ensure_ascii=False))
    assert payload["project_name"] == "中力越南项目"
    assert len(payload["items"]) == 1

    # 文件里没写项目名：null / 空串 / 缺字段 → 均为 None
    for raw in ('{"projectName": null, "items": []}', '{"projectName": "  ", "items": []}', '{"items": []}'):
        assert parse_llm_payload(raw)["project_name"] is None


# ── 匹配与分桶 ────────────────────────────────────────────

def test_match_fill_overwrite_unmatched():
    flat = flatten_tree(_tree())
    items = [
        _item("ERP模块", "SAP ECC", "ERP模块"),              # 末级且为空 → 将填写
        _item("客户信息", "浙江中力", "客户信息"),            # 已有内容 → 将覆盖
        _item("ERP模块", "重复条目", "ERP模块"),              # 同一节点第二次 → 去重
        _item("设备数量", "3 台", None, "硬件 / 车辆"),       # 匹配不上 → 未匹配（建议归属不在树里）
        _item("节拍", "60 秒", None, "基础信息 / 订单信息"),  # 匹配不上 → 未匹配（建议归属存在）
    ]
    result = match_items(flat, items)

    assert [row["node_id"] for row in result["fill"]] == ["c211"]
    assert result["fill"][0]["value"] == "SAP ECC"
    assert result["fill"][0]["path"] == "基础信息 / 订单信息 / ERP / ERP模块"

    assert [row["node_id"] for row in result["overwrite"]] == ["c1"]
    assert result["overwrite"][0]["current"] == "中力"
    assert result["overwrite"][0]["value"] == "浙江中力"

    assert len(result["unmatched"]) == 2
    assert result["unmatched"][0]["suggested_parent_id"] is None
    assert result["unmatched"][1]["suggested_parent_id"] == "c2"
    assert result["unmatched"][1]["suggested_parent_path"] == "基础信息 / 订单信息"


def test_match_fuzzy_threshold_and_identical_skip():
    flat = flatten_tree(_tree())
    # 「尺寸」少一个字：规范化后相似度 ≈0.94 ≥ 0.9 → 命中
    fuzzy = match_items(flat, [_item("通道与托盘间距尺", "3 米")])
    assert [row["node_id"] for row in fuzzy["fill"]] == ["n2"]

    # 相差过多的标题不得命中（相似度低于阈值）
    far = match_items(flat, [_item("通道尺寸", "3 米")])
    assert far["fill"] == [] and len(far["unmatched"]) == 1

    # 与现有内容一致 → 不产生任何变更
    same = match_items(flat, [_item("公网ip", "10.0.0.1")])
    assert same == {"fill": [], "overwrite": [], "unmatched": []}


def test_match_select_requires_valid_option():
    flat = flatten_tree(_tree())
    ok = match_items(flat, [_item("项目类型", "试点项目", "项目类型")])
    assert [row["node_id"] for row in ok["fill"]] == ["c3"]
    assert ok["fill"][0]["content_type"] == "select"

    # 值不在可选项里 → 按未匹配处理
    bad = match_items(flat, [_item("项目类型", "大客户项目", "项目类型")])
    assert bad["fill"] == []
    assert bad["unmatched"][0]["value"] == "大客户项目"


def test_resolve_parent_depth_cap_and_fallback():
    flat = flatten_tree(_tree())
    # 归属到第 4 层节点 → 新节点会超深，自动上提到第 3 层
    parent_id, parent_path = resolve_parent(flat, "基础信息 / 订单信息 / ERP / ERP模块")
    assert parent_id == "c21"
    assert parent_path == "基础信息 / 订单信息 / ERP"

    # 整条路径对不上 → 退化为最后一段标题全局找
    parent_id, parent_path = resolve_parent(flat, "某某 / 公网ip")
    assert parent_id == "n1"

    assert resolve_parent(flat, None) == (None, None)
    assert resolve_parent(flat, "不存在的节点") == (None, None)
