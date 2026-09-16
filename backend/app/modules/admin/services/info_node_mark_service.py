"""项目信息树节点「关注」标注 Service（项目动态的个人订阅源）。

用户在项目详情页「项目信息管理」展示卡上点子节点右侧的星标即关注该节点；
被关注节点的**最新一条**变动（project_info_node_change 里该节点最新记录）
展示在同页「项目动态」卡里——只展示变动内容（detail），不带时间与人员
（对照原型 ProjectActivityCard 的「关注节点变动」分组，用户明确要求）。

**按人隔离**（用户口径：「自己关注的自己才能看到，每个人可能关注的节点不一样」）：
关注列表以 (node_id, operator) 为主键，星标状态、项目动态都按当前登录人过滤；
operator 取 JWT sub（网关管控路由，识别不到用户身份的请求由 API 层拒绝）。

清理：节点（含子树）被删除、整树被导入/模板重建替换时，**所有人**对该节点的
标注随节点一起删除（remove_marks / clear_project_marks 在业务事务里调用），
避免留下点不开的孤儿关注。

查询接口见 api/info_nodes.py：
  GET  /info-nodes/projects/{id}/marks     当前用户被关注的节点 id 列表（前端星标状态）
  POST /info-nodes/nodes/{node_id}/mark    切换当前用户的关注状态
  GET  /info-nodes/projects/{id}/activity  项目动态（当前用户被关注节点的最新变动）
"""
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.modules.admin.models_das.models import ProjectInfoNode, ProjectInfoNodeChange, ProjectInfoNodeMark
from app.modules.admin.utils_das.config import DATABASE_URL

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 项目动态一次最多返回多少条（每个被关注节点至多一条，正常远小于此）
ACTIVITY_LIMIT = 50


def _now_str() -> str:
    """与 delivery.py / info_node_service 一致，用字符串存时间戳。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── 清理（与业务同事务，由调用方 commit；不等于「取消关注」，按节点全量删） ──


def remove_marks(db, node_ids: Optional[Iterable[str]]) -> None:
    """删除给定节点的关注标注（节点被删时调用；不 commit）。"""
    ids = [node_id for node_id in (node_ids or []) if node_id]
    if not ids:
        return
    db.query(ProjectInfoNodeMark).filter(
        ProjectInfoNodeMark.node_id.in_(ids)
    ).delete(synchronize_session=False)


def clear_project_marks(db, project_id: str) -> None:
    """清空某项目的全部关注标注（整树被替换时调用；不 commit）。"""
    db.query(ProjectInfoNodeMark).filter(
        ProjectInfoNodeMark.project_id == project_id
    ).delete(synchronize_session=False)


def root_title_of(node_id: Optional[str], nodes: Dict[str, Dict[str, Any]]) -> str:
    """沿 parent_id 向上走到的根节点标题（纯函数，便于单测）。

    nodes: {节点id: {"title": ..., "parent_id": ...}}（当前树快照）。
    节点不存在 / 链断了 / 成环（超过 32 层）返回空串，由调用方回退到节点自身标题。
    """
    current = node_id
    title = ""
    for _ in range(32):
        if not current:
            return title  # 走到最顶层
        node = nodes.get(current)
        if node is None:
            return ""  # 节点不存在或 parent 指向了已删除的节点
        title = node.get("title") or title
        current = node.get("parent_id")
    return ""  # 超过 32 层：数据成环，放弃


class InfoNodeMarkService:
    """关注标注的读写（按 operator 隔离）；activity 由标注 ⋈ 操作记录聚合而成。"""

    def list_for_project(self, project_id: str, operator: str) -> List[str]:
        """某项目里 operator 关注的节点 id 列表（前端据此点亮星标）。"""
        db = SessionLocal()
        try:
            rows = db.query(ProjectInfoNodeMark.node_id).filter(
                ProjectInfoNodeMark.project_id == project_id,
                ProjectInfoNodeMark.operator == operator,
            ).all()
            return [row[0] for row in rows]
        finally:
            db.close()

    def toggle(self, node_id: str, operator: str,
               operator_name: Optional[str] = None) -> bool:
        """切换 operator 对该节点的关注状态，返回切换后是否被关注。

        节点不存在抛 LookupError（调用方转 404）——前端树可能已过期。
        只动自己那一行：别人的关注不受影响。
        """
        db = SessionLocal()
        try:
            node = db.query(ProjectInfoNode).filter(
                ProjectInfoNode.id == node_id
            ).first()
            if not node:
                raise LookupError("节点不存在")

            existing = db.query(ProjectInfoNodeMark).filter(
                ProjectInfoNodeMark.node_id == node_id,
                ProjectInfoNodeMark.operator == operator,
            ).first()
            if existing:
                db.delete(existing)
                db.commit()
                return False

            db.add(ProjectInfoNodeMark(
                node_id=node_id,
                operator=operator,
                project_id=node.project_id,
                operator_name=operator_name,
                created_at=_now_str(),
            ))
            db.commit()
            return True
        finally:
            db.close()

    def marked_activity(self, project_id: str, operator: str,
                        limit: int = ACTIVITY_LIMIT) -> List[Dict[str, Any]]:
        """项目动态：operator 关注的每个节点只取最新一条变动，整体最新在前。

        标题用**当前**树里的节点/根节点标题（记录里的 node_title 只是当时的快照）；
        节点没记过任何操作则不出现在动态里（无变动可展示）。
        """
        db = SessionLocal()
        try:
            node_ids = [row[0] for row in db.query(ProjectInfoNodeMark.node_id).filter(
                ProjectInfoNodeMark.project_id == project_id,
                ProjectInfoNodeMark.operator == operator,
            ).all()]
            if not node_ids:
                return []

            rows = db.query(ProjectInfoNodeChange).filter(
                ProjectInfoNodeChange.project_id == project_id,
                ProjectInfoNodeChange.node_id.in_(node_ids),
            ).order_by(
                ProjectInfoNodeChange.created_at.desc(), ProjectInfoNodeChange.id.desc(),
            ).all()

            # 记录已按时间倒序：每个节点第一次出现的就是它最新的一条变动
            seen = set()
            latest = []
            for row in rows:
                if row.node_id in seen:
                    continue
                seen.add(row.node_id)
                latest.append(row)
                if len(latest) >= limit:
                    break

            nodes = {
                item.id: {"title": item.title, "parent_id": item.parent_id}
                for item in db.query(ProjectInfoNode).filter(
                    ProjectInfoNode.project_id == project_id
                ).all()
            }

            activity = []
            for row in latest:
                node = nodes.get(row.node_id) or {}
                root_title = root_title_of(row.node_id, nodes)
                node_title = node.get("title") or row.node_title or ""
                activity.append({
                    "node_id": row.node_id,
                    # 只展示变动内容（不带时间与人员）；标题仅用于说明「这是哪个节点」
                    "node_title": node_title,
                    "root_title": root_title or node_title,
                    "action": row.action,
                    "detail": row.detail or "",
                    "created_at": row.created_at,
                })
            return activity
        finally:
            db.close()


info_node_mark_service = InfoNodeMarkService()
