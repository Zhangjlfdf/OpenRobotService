"""项目信息树节点操作记录 Service（编辑历史 / 审计）。

每个节点操作写一条 project_info_node_change：时间 / 人员 / 节点 / 具体变动，
detail 在写入时由本模块拼成人话（前端直接展示，不必自己再组织文案）。

展示归属（与前端「编辑历史」弹层一致）：
  - 节点 X 的历史 = node_id=X 的记录 + 「parent_id=X 且 action=delete」的记录；
  - 删除记录挂在被删节点的**上级节点**上——节点删掉后自身记录查不到，
    用户要求「删除节点在其上级节点显示删除记录」；
  - 整树级操作（批量导入 / 模板重建 / 详情模板同步）node_id 为 NULL，
    一条记录说明整树发生了什么，不逐节点刷屏。

写入与业务同事务：info_node_service 在同一个 Session 里 add 记录再统一 commit，
避免出现「节点改了但没记」或「记了但节点没改」。查询接口见
api/info_nodes.py 的 GET /info-nodes/projects/{id}/changes[/summary]。
"""
import json
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, or_

from app.core.db import SessionLocal  # 共享引擎（pool_pre_ping/pool_recycle），见 app/core/db.py
from app.modules.admin.models_das.models import ProjectInfoNodeChange

# 操作类型（与前端标签一一对应）
ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_MOVE = "move"
ACTION_DELETE = "delete"
ACTION_IMPORT = "import"
ACTION_SYNC = "sync"

# 内容形式的中文名（与前端 CONTENT_TYPE_NAMES 一致）
CONTENT_TYPE_LABELS = {
    "text": "文字输入",
    "select": "下拉选择",
    "file": "上传文件",
    "image": "上传图片",
}

# detail 里单个值的展示上限，超出截断加省略号（完整值仍在节点本身上）
MAX_VALUE_DISPLAY = 40


def _now_str() -> str:
    """与 delivery.py / info_node_service 一致，用字符串存时间戳。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_id() -> str:
    """时间有序的 UUID（v7）：created_at 只精确到秒，同一秒内的先后靠 id 兜底排序。"""
    uuid7 = getattr(uuid, "uuid7", None)
    if uuid7 is not None:
        return str(uuid7())
    ms = int(time.time() * 1000)
    rand = uuid.uuid4().int & ((1 << 74) - 1)
    return str(uuid.UUID(int=(ms << 80) | (0x7 << 76) | rand))


def _try_json(raw: Any) -> Any:
    """宽松解析 TEXT 里的 JSON；非 JSON / 非字符串返回 None。"""
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _short(text: str, limit: int = MAX_VALUE_DISPLAY) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _type_label(content_type: Optional[str]) -> str:
    return CONTENT_TYPE_LABELS.get(content_type or "text", content_type or "文字输入")


def describe_value(content_type: Optional[str], raw: Any) -> str:
    """节点值（库里的 TEXT）→ 人话文本。

    text 原文（空白折叠）；select 取已选项；file/image 尽量取文件名；
    坏数据（如 select 里存了非 JSON）按原文兜底，不抛异常。
    """
    if raw is None or raw == "":
        return ""
    if not isinstance(raw, str):
        raw = str(raw)
    if content_type == "select":
        parsed = _try_json(raw)
        if isinstance(parsed, dict):
            selected = parsed.get("selected")
            return selected.strip() if isinstance(selected, str) else ""
        return raw.strip()
    if content_type in ("file", "image"):
        parsed = _try_json(raw)
        if isinstance(parsed, dict):
            for key in ("name", "file_name"):
                picked = parsed.get(key)
                if isinstance(picked, str) and picked.strip():
                    return picked.strip()
        return ""
    return " ".join(raw.split())


# ── detail 文案（纯函数，便于单测） ─────────────────────


def build_update_detail(old: Dict[str, Any], new: Dict[str, Any]) -> str:
    """拼「改了什么」：标题 / 内容形式 / 内容 / 同级顺序逐项对比，无变化返回空串。

    old 为改动前的节点字段快照，new 为本次提交的字段（只比较 new 里出现的键）。
    """
    parts: List[str] = []
    old_title = old.get("title") or ""
    old_type = old.get("content_type") or "text"

    if "title" in new and (new.get("title") or "") != old_title:
        parts.append(f"把标题从「{old_title}」改为「{new.get('title') or ''}」")

    new_type = new.get("content_type", old_type)
    if "content_type" in new and new_type != old_type:
        parts.append(f"把内容形式从「{_type_label(old_type)}」改为「{_type_label(new_type)}」")

    if "value" in new and (new.get("value") or "") != (old.get("value") or ""):
        old_disp = _short(describe_value(old_type, old.get("value"))) or "空"
        new_disp = _short(describe_value(new_type, new.get("value"))) or "空"
        parts.append(f"把内容从「{old_disp}」改为「{new_disp}」")

    if "sort_order" in new and new.get("sort_order") != old.get("sort_order"):
        parts.append(f"调整了同级顺序（{old.get('sort_order')} → {new.get('sort_order')}）")

    return "；".join(parts)


def build_create_detail(title: str) -> str:
    return f"新建节点「{title}」"


def build_delete_detail(title: str, child_count: int) -> str:
    if child_count > 0:
        return f"删除节点「{title}」及其 {child_count} 个子节点"
    return f"删除节点「{title}」"


def build_move_detail(
    title: str,
    old_parent_title: Optional[str],
    new_parent_title: Optional[str],
    same_parent: bool = False,
) -> str:
    if same_parent:
        return f"调整了「{title}」在同级中的顺序"
    if not new_parent_title:
        return f"把「{title}」从「{old_parent_title or '最外层'}」移到最外层"
    if not old_parent_title:
        return f"把「{title}」从最外层移到「{new_parent_title}」下"
    return f"把「{title}」从「{old_parent_title}」移到「{new_parent_title}」下"


def build_import_detail(source: str, added: int, removed: int) -> str:
    if source == "template":
        return f"按预设模板重建信息树：写入 {added} 个节点（原有 {removed} 个节点被替换）"
    return f"导入信息树：写入 {added} 个节点（原有 {removed} 个节点被替换）"


def build_sync_detail(added: int, updated: int, deleted: int) -> str:
    return f"详情模板同步：新增 {added} 个、更新 {updated} 个、删除 {deleted} 个节点"


# ── 写入（与业务同事务，由调用方 commit） ────────────────


def add_change(
    db,
    *,
    project_id: str,
    action: str,
    detail: str,
    node_id: Optional[str] = None,
    parent_id: Optional[str] = None,
    node_title: str = "",
    operator: Optional[str] = None,
    operator_name: Optional[str] = None,
    created_at: Optional[str] = None,
) -> None:
    """把一条操作记录加入调用方的 Session（不 commit，由业务事务统一提交）。"""
    db.add(ProjectInfoNodeChange(
        id=_new_id(),
        project_id=project_id,
        node_id=node_id,
        parent_id=parent_id,
        node_title=(node_title or "")[:255],
        action=action,
        operator=operator,
        operator_name=operator_name,
        detail=detail,
        created_at=created_at or _now_str(),
    ))


# ── 查询 ───────────────────────────────────────────────


def _to_dict(row: ProjectInfoNodeChange) -> Dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "node_id": row.node_id,
        "parent_id": row.parent_id,
        "node_title": row.node_title,
        "action": row.action,
        "operator": row.operator,
        "operator_name": row.operator_name,
        "detail": row.detail,
        "created_at": row.created_at,
    }


class InfoNodeChangeService:
    """操作记录的读写（写入走 add_change，与业务同事务）。"""

    def list_for_node(self, project_id: str, node_id: str, limit: int = 100) -> List[Dict]:
        """某节点的编辑历史：自身记录 + 直接子节点的删除记录（最新在前）。"""
        db = SessionLocal()
        try:
            rows = db.query(ProjectInfoNodeChange).filter(
                ProjectInfoNodeChange.project_id == project_id,
                or_(
                    ProjectInfoNodeChange.node_id == node_id,
                    and_(
                        ProjectInfoNodeChange.parent_id == node_id,
                        ProjectInfoNodeChange.action == ACTION_DELETE,
                    ),
                ),
            ).order_by(
                ProjectInfoNodeChange.created_at.desc(), ProjectInfoNodeChange.id.desc(),
            ).limit(limit).all()
            return [_to_dict(row) for row in rows]
        finally:
            db.close()

    def list_project_changes(self, project_id: str, limit: int = 100) -> List[Dict]:
        """项目全部操作记录（最新在前），含整树级记录（node_id 为 NULL）。"""
        db = SessionLocal()
        try:
            rows = db.query(ProjectInfoNodeChange).filter(
                ProjectInfoNodeChange.project_id == project_id,
            ).order_by(
                ProjectInfoNodeChange.created_at.desc(), ProjectInfoNodeChange.id.desc(),
            ).limit(limit).all()
            return [_to_dict(row) for row in rows]
        finally:
            db.close()

    def latest_by_node(self, project_id: str) -> Dict[str, str]:
        """各节点最新记录的 id：{节点id: 记录id}，供前端算「小红点」。

        归属规则同 list_for_node：删除记录计入其上级节点。
        记录 id 是时间有序的 UUIDv7（见 _new_id），max(id) 即最新一条——不用 created_at
        是因为它只到秒，同秒内的新记录按时间比会漏（点开过、同一秒又有新改动就不出红点）。
        前端拿这个 id 与本机已读水位做相等比较：一致=看过，不一致=有新变动。
        """
        db = SessionLocal()
        try:
            latest: Dict[str, str] = {}
            own = db.query(
                ProjectInfoNodeChange.node_id,
                func.max(ProjectInfoNodeChange.id),
            ).filter(
                ProjectInfoNodeChange.project_id == project_id,
                ProjectInfoNodeChange.node_id.isnot(None),
            ).group_by(ProjectInfoNodeChange.node_id).all()
            for node_id, record_id in own:
                if node_id and record_id:
                    latest[node_id] = record_id

            deleted = db.query(
                ProjectInfoNodeChange.parent_id,
                func.max(ProjectInfoNodeChange.id),
            ).filter(
                ProjectInfoNodeChange.project_id == project_id,
                ProjectInfoNodeChange.action == ACTION_DELETE,
                ProjectInfoNodeChange.parent_id.isnot(None),
            ).group_by(ProjectInfoNodeChange.parent_id).all()
            for parent_id, record_id in deleted:
                if parent_id and record_id and record_id > latest.get(parent_id, ""):
                    latest[parent_id] = record_id
            return latest
        finally:
            db.close()


info_node_change_service = InfoNodeChangeService()
