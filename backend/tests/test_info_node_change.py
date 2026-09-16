"""节点操作记录（编辑历史）纯函数测试 —— 不连库。

覆盖 detail 文案的两块核心：节点值 → 人话（text/select/file/坏数据），
以及各操作类型（修改逐字段对比 / 新建 / 删除含子树 / 移动换父 / 整树导入·同步）的表述。
前端「编辑历史」弹层直接展示这些文案，改文案即改前端可见内容。
"""
import uuid

from app.modules.admin.services.info_node_change_service import (
    _new_id,
    build_create_detail,
    build_delete_detail,
    build_import_detail,
    build_move_detail,
    build_sync_detail,
    build_update_detail,
    describe_value,
)


class TestDescribeValue:
    def test_text_折叠空白(self):
        assert describe_value("text", "  4 台\n叉车  ") == "4 台 叉车"

    def test_select_取已选项(self):
        assert describe_value("select", '{"selected": "试点项目", "options": ["试点项目", "PK项目"]}') == "试点项目"

    def test_select_未选择为空(self):
        assert describe_value("select", '{"selected": "", "options": ["A"]}') == ""

    def test_select_坏数据兜底原文(self):
        assert describe_value("select", "试点项目") == "试点项目"

    def test_file_取文件名(self):
        assert describe_value("file", '{"name": "方案.pdf", "resource_id": 7}') == "方案.pdf"
        assert describe_value("image", '{"file_name": "现场.png"}') == "现场.png"

    def test_file_空对象为空(self):
        assert describe_value("file", "{}") == ""

    def test_空值(self):
        assert describe_value("text", None) == ""
        assert describe_value("text", "") == ""


class TestUpdateDetail:
    def _old(self):
        return {"title": "车辆配置", "content_type": "text", "value": "4 台叉车", "sort_order": 2}

    def test_无实际变动返回空串(self):
        old = self._old()
        assert build_update_detail(old, {"title": "车辆配置", "value": "4 台叉车"}) == ""
        # value 的 None 与空串视为同一状态（前端清空内容）
        assert build_update_detail({**old, "value": None}, {"value": ""}) == ""

    def test_改标题(self):
        assert build_update_detail(self._old(), {"title": "车辆"}) == "把标题从「车辆配置」改为「车辆」"

    def test_改内容(self):
        detail = build_update_detail(self._old(), {"value": "6 台叉车（含 2 台备用）"})
        assert detail == "把内容从「4 台叉车」改为「6 台叉车（含 2 台备用）」"

    def test_清空内容显示空(self):
        assert build_update_detail(self._old(), {"value": ""}) == "把内容从「4 台叉车」改为「空」"

    def test_改内容形式(self):
        detail = build_update_detail(self._old(), {"content_type": "select", "value": '{"selected":"", "options":[]}'})
        assert detail == "把内容形式从「文字输入」改为「下拉选择」；把内容从「4 台叉车」改为「空」"

    def test_改顺序(self):
        assert build_update_detail(self._old(), {"sort_order": 5}) == "调整了同级顺序（2 → 5）"

    def test_多字段一次改动拼接(self):
        detail = build_update_detail(self._old(), {"title": "车辆", "value": "6 台"})
        assert detail == "把标题从「车辆配置」改为「车辆」；把内容从「4 台叉车」改为「6 台」"

    def test_超长值截断(self):
        detail = build_update_detail({"title": "T", "content_type": "text", "value": "旧"},
                                     {"value": "长" * 100})
        assert "…" in detail
        assert len(detail) < 120


class TestOtherDetails:
    def test_新建(self):
        assert build_create_detail("充电区位置") == "新建节点「充电区位置」"

    def test_删除_无子节点(self):
        assert build_delete_detail("充电区位置", 0) == "删除节点「充电区位置」"

    def test_删除_含子树(self):
        assert build_delete_detail("车辆", 3) == "删除节点「车辆」及其 3 个子节点"

    def test_移动_换父(self):
        assert build_move_detail("叉车1", "车辆", "设备") == "把「叉车1」从「车辆」移到「设备」下"

    def test_移动_移到最外层(self):
        assert build_move_detail("叉车1", "车辆", None) == "把「叉车1」从「车辆」移到最外层"

    def test_移动_从最外层挂到某节点(self):
        assert build_move_detail("叉车1", None, "车辆") == "把「叉车1」从最外层移到「车辆」下"

    def test_移动_同父同级排序(self):
        assert build_move_detail("叉车1", "车辆", "车辆", same_parent=True) == "调整了「叉车1」在同级中的顺序"

    def test_整树导入与模板重建文案不同(self):
        assert build_import_detail("import", 120, 121) == "导入信息树：写入 120 个节点（原有 121 个节点被替换）"
        assert build_import_detail("template", 120, 0) == "按预设模板重建信息树：写入 120 个节点（原有 0 个节点被替换）"

    def test_模板同步(self):
        assert build_sync_detail(2, 5, 1) == "详情模板同步：新增 2 个、更新 5 个、删除 1 个节点"


class TestRecordId:
    """记录 id 用时间有序的 UUID：created_at 只到秒，同秒多条的展示顺序靠 id 兜底。"""

    def test_是合法_uuid_且随时间递增(self):
        ids = [_new_id() for _ in range(5)]
        for value in ids:
            assert uuid.UUID(value).version == 7
        assert ids == sorted(ids)

    def test_同毫秒也不重复(self):
        assert len({_new_id() for _ in range(200)}) == 200
