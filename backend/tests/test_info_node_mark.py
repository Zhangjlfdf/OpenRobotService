"""节点「关注」标注 + 项目动态聚合的测试。

纯函数部分（root_title_of）不连库；toggle/清理用假 session 验证 SQL 行为。
运行方式（反射 runner；**必须先 import app.core.db**——conftest 会把 create_engine
换成 MagicMock，若任由 admin 包在之后懒加载 app.core.db，event.listen 会对
MagicMock 引擎报 InvalidRequestError）：
    python -c "
    import app.core.db
    import tests.conftest
    import tests.test_info_node_mark as t
    import inspect
    n=0
    for name, obj in vars(t).items():
        if inspect.isclass(obj) and name.startswith('Test'):
            inst = obj()
            for m in dir(obj):
                if m.startswith('test_'): getattr(inst, m)(); n+=1
    print('PASS', n)
    "
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import app.modules.admin.services.info_node_mark_service as marks_mod
from app.modules.admin.services.info_node_mark_service import (
    clear_project_marks,
    remove_marks,
    root_title_of,
)


def _nodes(*pairs):
    """pairs: (id, title, parent_id) → 服务里用的 {id: {title, parent_id}} 快照。"""
    return {nid: {"title": title, "parent_id": parent} for nid, title, parent in pairs}


class TestRootTitleOf:
    def test_子节点向上取到根标题(self):
        nodes = _nodes(
            ("r1", "基础信息", None),
            ("c1", "客户信息", "r1"),
            ("g1", "联系人", "c1"),
        )
        assert root_title_of("c1", nodes) == "基础信息"
        # 多层同样走到最顶
        assert root_title_of("g1", nodes) == "基础信息"

    def test_根节点取自身标题(self):
        nodes = _nodes(("r1", "基础信息", None))
        assert root_title_of("r1", nodes) == "基础信息"

    def test_节点不存在返回空串(self):
        nodes = _nodes(("r1", "基础信息", None))
        assert root_title_of("missing", nodes) == ""

    def test_parent链断返回空串(self):
        # c1 的父节点已不在快照里：链断了，不能瞎猜根
        nodes = _nodes(("c1", "客户信息", "gone"))
        assert root_title_of("c1", nodes) == ""

    def test_成环返回空串(self):
        nodes = _nodes(("a", "A", "b"), ("b", "B", "a"))
        assert root_title_of("a", nodes) == ""

    def test_parent为空串视为最顶层(self):
        nodes = _nodes(("r1", "基础信息", ""))
        assert root_title_of("r1", nodes) == "基础信息"

    def test_节点标题为空时用上层标题兜底(self):
        nodes = _nodes(("r1", "硬件", None), ("c1", "", "r1"))
        assert root_title_of("c1", nodes) == "硬件"


class _FakeSessionFactory:
    """把模块级 SessionLocal 换成返回固定假 session 的工厂，用完还原。"""

    def __init__(self, fake_db):
        self.fake_db = fake_db
        self.old = None

    def __enter__(self):
        self.old = marks_mod.SessionLocal
        marks_mod.SessionLocal = lambda: self.fake_db
        return self

    def __exit__(self, *exc):
        marks_mod.SessionLocal = self.old


class TestToggle:
    def test_未关注的节点切换为关注(self):
        # query().filter().first() 连查两次：先节点、后既有标注（没有）
        node = SimpleNamespace(project_id="p1")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [node, None]
        with _FakeSessionFactory(db):
            marked = marks_mod.InfoNodeMarkService().toggle("n1", operator="u", operator_name="U")

        assert marked is True
        added = db.add.call_args[0][0]
        assert added.node_id == "n1" and added.project_id == "p1"
        assert added.operator == "u" and added.operator_name == "U"
        assert added.created_at  # 'YYYY-MM-DD HH:MM:SS'
        db.commit.assert_called_once()
        db.close.assert_called_once()

    def test_已关注的节点取消关注(self):
        existing = MagicMock()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [
            SimpleNamespace(project_id="p1"), existing,
        ]
        with _FakeSessionFactory(db):
            marked = marks_mod.InfoNodeMarkService().toggle("n1", operator="u")

        assert marked is False
        db.delete.assert_called_once_with(existing)
        db.add.assert_not_called()
        db.commit.assert_called_once()

    def test_查既有标注只按本人过滤(self):
        # 取消/判定时 WHERE 必须同时带 node_id 与 operator：绝不能删掉别人的关注
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [
            SimpleNamespace(project_id="p1"), None,
        ]
        with _FakeSessionFactory(db):
            marks_mod.InfoNodeMarkService().toggle("n1", operator="u")

        clause = " AND ".join(str(a) for a in db.query.return_value.filter.call_args_list[1][0])
        assert "project_info_node_mark.node_id" in clause
        assert "project_info_node_mark.operator" in clause

    def test_节点不存在抛LookupError(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with _FakeSessionFactory(db):
            try:
                marks_mod.InfoNodeMarkService().toggle("missing", operator="u")
                raised = False
            except LookupError:
                raised = True
        assert raised
        db.commit.assert_not_called()
        db.close.assert_called_once()  # finally 里也要关掉


class TestListForProject:
    def test_只返回本人关注的节点id(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [("n1",), ("n2",)]
        with _FakeSessionFactory(db):
            ids = marks_mod.InfoNodeMarkService().list_for_project("p1", "u")

        assert ids == ["n1", "n2"]
        clause = " AND ".join(str(a) for a in db.query.return_value.filter.call_args_list[0][0])
        assert "project_info_node_mark.project_id" in clause
        assert "project_info_node_mark.operator" in clause
        db.close.assert_called_once()


class TestCleanup:
    def test_remove_marks批量删除并过滤空id(self):
        db = MagicMock()
        remove_marks(db, ["a", "", None, "b"])
        db.query.return_value.filter.return_value.delete.assert_called_once_with(
            synchronize_session=False,
        )
        # 过滤后 in_ 里只剩真实的两个 id
        args = db.query.return_value.filter.call_args[0]
        assert len(args) == 1  # 一个 in_ 条件

    def test_remove_marks空列表不碰库(self):
        db = MagicMock()
        remove_marks(db, [])
        remove_marks(db, None)
        db.query.assert_not_called()

    def test_clear_project_marks按项目删除(self):
        db = MagicMock()
        clear_project_marks(db, "p1")
        db.query.return_value.filter.return_value.delete.assert_called_once_with(
            synchronize_session=False,
        )
