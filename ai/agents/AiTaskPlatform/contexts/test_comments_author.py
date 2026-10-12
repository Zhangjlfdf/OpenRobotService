"""讨论史作者名还原：created_by 是 users.id/username，必须还原成真实姓名。

回归背景：TaskComment 模型没有 created_by_name 列（后端 API 的该字段是派生值），
AI 侧原先直接取 created_by，导致微信用户被渲染成 wechat_{openid[:10]} 这种原始
ID（讨论区 UI 显示的是真实姓名），模型只能靠正文猜人名，猜不出就被自评判为幻构。

注：本文件用合成占位 ID/姓名，不引用任何真实用户数据。
"""
import sys

from ai.agents.AiTaskPlatform.contexts.comments import (
    _author_display_map,
    _rows_from_comments,
    render_comment_lines,
)


class _C:
    """替身：只需要 created_by / content 两个属性。"""

    def __init__(self, created_by, content="正文"):
        self.created_by = created_by
        self.content = content


def test_wechat_author_resolved_to_real_name():
    rows = _rows_from_comments(
        [_C("wechat_AbCdEfGhIj", "小屏页面是否需要适配？")],
        {"wechat_AbCdEfGhIj": "张三"},
    )
    assert rows[0]["author"] == "张三"


def test_render_uses_resolved_name():
    rows = _rows_from_comments(
        [_C("wechat_AbCdEfGhIj", "小屏页面是否需要适配？")],
        {"wechat_AbCdEfGhIj": "张三"},
    )
    assert render_comment_lines(rows) == ["[张三] 小屏页面是否需要适配？"]


def test_internal_user_resolved_too():
    """非微信用户（created_by=admin）同样要吃映射。"""
    rows = _rows_from_comments([_C("admin", "问")], {"admin": "管理员"})
    assert rows[0]["author"] == "管理员"


def test_unknown_author_keeps_raw_id():
    """映射里没有的作者不得被吞掉，保留原始 ID 便于排查。"""
    rows = _rows_from_comments(
        [_C("wechat_unknown", "hi")], {"wechat_AbCdEfGhIj": "张三"}
    )
    assert rows[0]["author"] == "wechat_unknown"


def test_u_teacher_author_preserved():
    """U老师不在用户表里，须原样保留（render_comment_lines 会特判它）。"""
    rows = _rows_from_comments([_C("U老师", "答")], {"admin": "管理员"})
    assert rows[0]["author"] == "U老师"
    assert render_comment_lines(rows) == ["[U老师] 答"]


def test_missing_created_by_falls_back_to_placeholder():
    rows = _rows_from_comments([_C(None, "x")], {})
    assert rows[0]["author"] == "?"


def test_empty_and_none_name_map_keep_raw():
    assert _rows_from_comments([_C("wechat_x")], {})[0]["author"] == "wechat_x"
    assert _rows_from_comments([_C("wechat_x")], None)[0]["author"] == "wechat_x"


def test_empty_comments_returns_empty():
    assert _rows_from_comments([], {"a": "b"}) == []
    assert _rows_from_comments(None, {}) == []


def test_author_display_map_degrades_on_import_failure(monkeypatch):
    """映射加载失败必须降级为空 dict，不能把讨论史读取一起搞挂。"""
    monkeypatch.setitem(sys.modules, "app.services.user_service", None)
    assert _author_display_map() == {}
